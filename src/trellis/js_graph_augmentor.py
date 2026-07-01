"""Augment a code-graph SQLite database with synthetic JS dynamic-dispatch edges."""

import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .js_pattern_indexer import JsPatternIndexer


def _normalize_path(path: str) -> str:
    """Return a POSIX-style relative path regardless of host OS."""
    return path.replace(os.sep, "/")


class JSGraphAugmentor:
    """Write JsPatternIndexer discoveries into code-graph's SQLite graph."""

    def __init__(self, project_path: str, db_path: str) -> None:
        self.project_path = Path(project_path).resolve()
        self.db_path = Path(db_path)
        self.indexer = JsPatternIndexer(str(self.project_path))

    def augment(self) -> Dict[str, int]:
        """Run pattern indexing and insert synthetic edges."""
        self.indexer.index()

        conn = sqlite3.connect(str(self.db_path))
        try:
            file_ids = self._load_file_ids(conn)
            functions = self._load_functions(conn)
            modules = self._load_modules(conn)
            classes = self._load_classes(conn)

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

            return inserted
        finally:
            conn.close()

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
        """Return (node_id, name, path, start_line, end_line) for function-like nodes."""
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
            for (path, node_id, _name, start, end) in functions
            if path == rel_path and start <= line <= end
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
        prefer_path: Optional[str] = None,
    ) -> Optional[int]:
        """Pick the best class node for a class name (case-insensitive)."""
        matches = [c for c in classes if c[1].lower() == class_name.lower()]
        if not matches:
            return None
        if prefer_path:
            for node_id, _name, path in matches:
                if prefer_path in path:
                    return node_id
        # Prefer non-test, non-example definitions; then prefer /ui/ or /component/
        for node_id, _name, path in matches:
            if (
                "/test" not in path
                and "/tests/" not in path
                and "/examples/" not in path
            ):
                return node_id
        return matches[0][0]

    def _insert_edge(
        self, conn: sqlite3.Connection, source_id: int, target_id: int, relation: str
    ) -> bool:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO edges (source_id, target_id, relation)
                VALUES (?, ?, ?)
                """,
                (source_id, target_id, relation),
            )
            return conn.total_changes > 0
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
        for path, node_id, name, _start, _end in functions:
            if path == rel_path and name == method_name:
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

            # Use file path to disambiguate which class definition is instantiated.
            # For maps like SUB_UI_COMPONENT used in ui.js, the class likely lives
            # under ui/; for graphics.js, under component/.
            prefer_path = None
            if "/ui.js" in edge.file_path:
                prefer_path = "/ui/"
            elif "/graphics.js" in edge.file_path:
                prefer_path = "/component/"

            # Edge to the class node
            class_id = self._class_node_id(classes, edge.target_name, prefer_path)
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
        for mapping in self.indexer.components.values():
            class_id = self._class_node_id(
                classes,
                mapping.class_name,
                prefer_path="/component/",
            )
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
            class_id = self._class_node_id(classes, class_name)
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
            class_id = self._class_node_id(classes, class_name)
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
