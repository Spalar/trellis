"""Augment a code-graph SQLite database with synthetic JS dynamic-dispatch edges."""

import os
import posixpath
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .graph_queries import confident_filter
from .js_pattern_indexer import JsPatternIndexer
from .utils import has_column, is_test_path


def _specifier_matches(spec: str, caller_path: str, target_path: str) -> bool:
    """Is `target_path` the file import specifier `spec` names from `caller_path`?

    Relative specifiers ('./x', '../x') resolve exactly; others (aliases
    like '@/ui/shape', or bare paths) match by path suffix.
    """
    stem = target_path.rsplit(".", 1)[0]
    if spec.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")):
        spec = spec.rsplit(".", 1)[0]
    if spec.startswith("."):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(caller_path), spec))
        return stem in (base, base + "/index")
    if spec.startswith("@") and "/" in spec:
        spec = spec.split("/", 1)[1]  # alias root: '@/ui/shape' -> 'ui/shape'
    return any(
        stem == s or stem.endswith("/" + s) for s in (spec, spec + "/index")
    )


def _normalize_path(path: str) -> str:
    """Return a POSIX-style relative path regardless of host OS."""
    return path.replace(os.sep, "/")


# Marks edges this augmentor owns, so each run can replace its previous ones.
OWNED_META = '{"trellis":"js"}'


class JSGraphAugmentor:
    """Write JsPatternIndexer discoveries into code-graph's SQLite graph."""

    def __init__(self, project_path: str, db_path: str) -> None:
        self.project_path = Path(project_path).resolve()
        self.db_path = Path(db_path)
        self.indexer = JsPatternIndexer(str(self.project_path))

    def augment(self) -> Dict[str, int]:
        """Run pattern indexing and insert synthetic edges."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            # Scan only the .js files code-graph indexed: walking the repo
            # also descends into envs, worktrees and build output.
            indexed = [
                self.project_path / row[0]
                for row in conn.execute("SELECT path FROM files WHERE path LIKE '%.js'")
            ]
            self.indexer.index(indexed)
            # Recompute from scratch: drop what the previous run inserted.
            conn.execute("DELETE FROM edges WHERE metadata = ?", (OWNED_META,))
            legacy_removed = self._remove_legacy_edges(conn)
            self._confident = confident_filter(conn, alias="")
            file_ids = self._load_file_ids(conn)
            functions = self._load_functions(conn)
            self._functions_by_path: Dict[str, List[Tuple]] = {}
            for fn in functions:
                self._functions_by_path.setdefault(fn[0], []).append(fn)
            modules = self._load_modules(conn)
            classes = self._load_classes(conn)
            self._imports = self._load_imports(conn)

            inserted = {
                "command_calls": 0,
                "instantiation_uses": 0,
                "component_uses": 0,
                "property_calls": 0,
                "this_method_calls": 0,
            }

            inserted["command_calls"] = self._add_command_call_edges(
                conn, file_ids, functions, modules
            )
            inserted["instantiation_uses"] = self._add_instantiation_edges(
                conn, file_ids, functions, modules, classes
            )
            inserted["component_uses"] = self._add_component_edges(
                conn, file_ids, functions, modules, classes
            )
            class_paths = {node_id: path for node_id, _name, path in classes}
            inserted["property_calls"] = self._add_property_call_edges(
                conn, file_ids, functions, modules, classes, class_paths
            )
            inserted["this_method_calls"] = self._add_this_method_call_edges(
                conn, file_ids, functions, modules, classes, class_paths
            )

            inserted["legacy_removed"] = legacy_removed
            return inserted
        finally:
            conn.close()

    @staticmethod
    def _remove_legacy_edges(conn: sqlite3.Connection) -> int:
        """Delete edges written by augmentor versions that did not mark them.

        Those rows have NULL metadata. Upstream's classification pass labels
        every cross-file call 'inferred' or 'ambiguous', so a cross-file call
        from a .js file still at the default 'extracted' with no metadata can
        only be a leftover from an older augmentor. Same-file leftovers are
        kept: they bound this.method() to the class in the calling file, which
        is correct. Indexes without edges.confidence can't be told apart and
        are left alone (a full `trellis sync` rebuild clears them).
        """
        if not has_column(conn, "edges", "confidence"):
            return 0
        cur = conn.execute(
            """
            DELETE FROM edges WHERE id IN (
                SELECT e.id
                FROM edges e
                JOIN nodes s ON e.source_id = s.id
                JOIN nodes t ON e.target_id = t.id
                JOIN files sf ON s.file_id = sf.id
                WHERE e.relation = 'calls' AND e.metadata IS NULL
                  AND e.confidence = 'extracted'
                  AND s.file_id != t.file_id
                  AND sf.path LIKE '%.js'
            )
            """
        )
        return cur.rowcount

    def _load_modules(
        self, conn: sqlite3.Connection
    ) -> List[Tuple[int, str, int, int]]:
        """Return (node_id, path, start_line, end_line) for module nodes."""
        return [
            (row[0], _normalize_path(row[1]), row[2], row[3])
            for row in conn.execute(
                """
                SELECT n.id, f.path, n.start_line, n.end_line
                FROM nodes n
                JOIN files f ON f.id = n.file_id
                WHERE n.type = 'module'
                """
            )
        ]

    def _load_file_ids(self, conn: sqlite3.Connection) -> Dict[str, int]:
        return {
            _normalize_path(row[1]): row[0]
            for row in conn.execute("SELECT id, path FROM files")
        }

    def _load_functions(
        self, conn: sqlite3.Connection
    ) -> List[Tuple[int, str, str, int, int]]:
        """Return (path, node_id, name, start_line, end_line) for function-like nodes."""
        return [
            (_normalize_path(row[0]), row[1], row[2], row[3], row[4])
            for row in conn.execute(
                """
                SELECT f.path, n.id, n.name, n.start_line, n.end_line
                FROM nodes n
                JOIN files f ON f.id = n.file_id
                WHERE n.type IN ('function', 'method')
                """
            )
        ]

    def _load_classes(self, conn: sqlite3.Connection) -> List[Tuple[int, str, str]]:
        """Return (node_id, name, path) for class nodes."""
        return [
            (row[0], row[1], _normalize_path(row[2]))
            for row in conn.execute(
                """
                SELECT n.id, n.name, f.path
                FROM nodes n
                JOIN files f ON f.id = n.file_id
                WHERE n.type = 'class'
                """
            )
        ]

    def _enclosing_function(
        self,
        functions: List[Tuple[int, str, str, int, int]],
        modules: List[Tuple[int, str, int, int]],
        rel_path: str,
        line: int,
    ) -> Optional[int]:
        """Find the innermost function/method (or module fallback) containing the line."""
        matches = [
            (node_id, start, end)
            for (_path, node_id, _name, start, end) in self._functions_by_path.get(rel_path, ())
            if start <= line <= end
        ]
        if matches:
            matches.sort(key=lambda x: x[2] - x[1])
            return matches[0][0]
        # Fallback to module node for top-level call sites
        for node_id, path, start, end in modules:
            if path == rel_path and start <= line <= end:
                return node_id
        return None

    def _class_node_id(
        self,
        classes: List[Tuple[int, str, str]],
        class_name: str,
        caller_path: Optional[str] = None,
    ) -> Optional[int]:
        """Pick the class node a name refers to from `caller_path` (case-insensitive).

        In order: a class in the caller's own file, the file the caller's
        `import` of that name points to, a class in any file the caller
        imports, then the one nearest the caller in the directory tree,
        preferring non-test code.
        """
        matches = [c for c in classes if c[1].lower() == class_name.lower()]
        if not matches:
            return None
        if caller_path:
            for node_id, _name, path in matches:
                if path == caller_path:
                    return node_id
            spec = self.indexer.imports.get(caller_path, {}).get(class_name)
            if spec:
                for node_id, _name, path in matches:
                    if _specifier_matches(spec, caller_path, path):
                        return node_id
            imported = self._imports.get(caller_path, set())
            for node_id, _name, path in matches:
                if path in imported:
                    return node_id
        caller_dir = (caller_path or "").split("/")[:-1]

        def shared(path: str) -> int:
            count = 0
            for a, b in zip(caller_dir, path.split("/")[:-1]):
                if a != b:
                    break
                count += 1
            return count

        best = min(matches, key=lambda c: (is_test_path(c[2]), -shared(c[2]), c[2]))
        return best[0]

    def _load_imports(self, conn: sqlite3.Connection) -> Dict[str, set]:
        """Caller file -> files it imports (from code-graph's import edges)."""
        imports: Dict[str, set] = {}
        rows = conn.execute(
            """
            SELECT DISTINCT sf.path, tf.path
            FROM edges e
            JOIN nodes s ON e.source_id = s.id JOIN files sf ON s.file_id = sf.id
            JOIN nodes t ON e.target_id = t.id JOIN files tf ON t.file_id = tf.id
            WHERE e.relation = 'imports' AND sf.id != tf.id
            """
        )
        for source, target in rows:
            imports.setdefault(_normalize_path(source), set()).add(_normalize_path(target))
        return imports

    def _insert_edge(
        self, conn: sqlite3.Connection, source_id: int, target_id: int, relation: str
    ) -> bool:
        """Ensure an edge exists; True when present afterwards.

        The code-graph indexer (>=0.156.0) extracts some dynamic-dispatch
        calls natively, so the edge may already exist — that still counts.
        New edges are stamped OWNED_META so the next run can replace them.
        """
        try:
            # An upstream 'ambiguous' guess for the same pair doesn't count:
            # the pattern match confirms it, so add a confident edge.
            exists = conn.execute(
                "SELECT 1 FROM edges WHERE source_id = ? AND target_id = ? AND relation = ? "
                + self._confident,
                (source_id, target_id, relation),
            ).fetchone()
            if exists:
                return True
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO edges (source_id, target_id, relation, metadata)
                VALUES (?, ?, ?, ?)
                """,
                (source_id, target_id, relation, OWNED_META),
            )
            return cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    def _add_command_call_edges(
        self,
        conn: sqlite3.Connection,
        file_ids: Dict[str, int],
        functions: List[Tuple[int, str, str, int, int]],
        modules: List[Tuple[int, str, int, int]],
    ) -> int:
        """Insert calls edges from ImageEditor API methods to command execute methods."""
        count = 0
        for edge in self.indexer.edges:
            if edge.relation != "executes":
                continue
            registration = self.indexer.commands.get(edge.target_name)
            if registration is None:
                continue

            caller_id = self._enclosing_function(
                functions, modules, edge.file_path, edge.line
            )
            execute_id = self._command_method_node(
                functions, registration.file_path, "execute"
            )
            if caller_id and execute_id:
                if self._insert_edge(conn, caller_id, execute_id, "calls"):
                    count += 1
        conn.commit()
        return count

    def _command_method_node(
        self,
        functions: List[Tuple[int, str, str, int, int]],
        rel_path: str,
        method_name: str,
    ) -> Optional[int]:
        for _path, node_id, name, _start, _end in self._functions_by_path.get(rel_path, ()):
            if name == method_name:
                return node_id
        return None

    def _add_instantiation_edges(
        self,
        conn: sqlite3.Connection,
        file_ids: Dict[str, int],
        functions: List[Tuple[int, str, str, int, int]],
        modules: List[Tuple[int, str, int, int]],
        classes: List[Tuple[int, str, str]],
    ) -> int:
        """Insert calls edges from instantiating functions to classes/constructors."""
        count = 0
        for edge in self.indexer.edges:
            if edge.relation != "instantiates":
                continue
            caller_id = self._enclosing_function(
                functions, modules, edge.file_path, edge.line
            )
            if caller_id is None:
                continue

            class_id = self._class_node_id(classes, edge.target_name, edge.file_path)
            if class_id and self._insert_edge(conn, caller_id, class_id, "calls"):
                count += 1

            # Edge to the constructor method in the same file as the class
            class_path = next(
                (path for node_id, _name, path in classes if node_id == class_id), None
            )
            if class_path:
                ctor_id = self._command_method_node(
                    functions, class_path, "constructor"
                )
                if ctor_id and self._insert_edge(conn, caller_id, ctor_id, "calls"):
                    count += 1

        conn.commit()
        return count

    def _add_component_edges(
        self,
        conn: sqlite3.Connection,
        file_ids: Dict[str, int],
        functions: List[Tuple[int, str, str, int, int]],
        modules: List[Tuple[int, str, int, int]],
        classes: List[Tuple[int, str, str]],
    ) -> int:
        """Insert calls edges from getComponent call sites to component classes."""
        count = 0
        for mapping in self.indexer.component_calls:
            class_id = self._class_node_id(classes, mapping.class_name, mapping.file_path)
            caller_id = self._enclosing_function(
                functions, modules, mapping.file_path, mapping.line
            )
            if caller_id and class_id:
                if self._insert_edge(conn, caller_id, class_id, "calls"):
                    count += 1
        conn.commit()
        return count

    def _add_property_call_edges(
        self,
        conn: sqlite3.Connection,
        file_ids: Dict[str, int],
        functions: List[Tuple[int, str, str, int, int]],
        modules: List[Tuple[int, str, int, int]],
        classes: List[Tuple[int, str, str]],
        class_paths: Dict[int, str],
    ) -> int:
        """Insert calls edges for this._prop.method() typed property calls."""
        count = 0
        for edge in self.indexer.edges:
            if edge.relation != "property_call":
                continue
            if "." not in edge.target_name:
                continue
            class_name, method_name = edge.target_name.split(".", 1)
            class_id = self._class_node_id(classes, class_name, edge.file_path)
            if class_id is None:
                continue
            class_path = class_paths.get(class_id)
            method_id = self._command_method_node(
                functions, class_path or "", method_name
            )
            caller_id = self._enclosing_function(
                functions, modules, edge.file_path, edge.line
            )
            if caller_id and method_id:
                if self._insert_edge(conn, caller_id, method_id, "calls"):
                    count += 1
        conn.commit()
        return count

    def _add_this_method_call_edges(
        self,
        conn: sqlite3.Connection,
        file_ids: Dict[str, int],
        functions: List[Tuple[int, str, str, int, int]],
        modules: List[Tuple[int, str, int, int]],
        classes: List[Tuple[int, str, str]],
        class_paths: Dict[int, str],
    ) -> int:
        """Insert calls edges for this._methodName() calls within the same class."""
        count = 0
        for edge in self.indexer.edges:
            if edge.relation != "this_method_call":
                continue
            if "." not in edge.target_name:
                continue
            class_name, method_name = edge.target_name.split(".", 1)
            class_id = self._class_node_id(classes, class_name, edge.file_path)
            if class_id is None:
                continue
            class_path = class_paths.get(class_id)
            method_id = self._command_method_node(
                functions, class_path or "", method_name
            )
            caller_id = self._enclosing_function(
                functions, modules, edge.file_path, edge.line
            )
            if caller_id and method_id:
                if self._insert_edge(conn, caller_id, method_id, "calls"):
                    count += 1
        conn.commit()
        return count
