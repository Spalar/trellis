"""Import-aware Python call-edge indexer.

Adds 'calls' edges to the code-graph index by reading each file's imports, so
`from services.billing import charge` binds to that module's function
rather than to the first `charge` anywhere in the repo. Calls it
cannot pin to exactly one definition (`obj.save()`, builtins, third-party
code) are left out instead of guessed.

Trellis owns every 'calls' edge whose metadata has a "caller" key. Each run
recomputes the full desired set and syncs it: stale or wrong owned edges are
deleted, missing ones inserted, and pairs upstream already links with a
confident edge are skipped. Parsed files are cached by mtime/size, so a
re-run after an edit only re-parses the edited files.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .graph_queries import confident_filter
from .utils import resolve_code_graph_db

# A call site, recorded relative to the caller's file:
#   ("name", "f")            f()
#   ("chain", ("a","b","f")) a.b.f()  (a is a plain name)
#   ("self", "f")            self.f() / cls.f()
#   ("super", "f")           super().f()
#   ("line", 12)             f() where f is a def nested in an enclosing function
CallSite = Tuple[str, object]

# What a name resolves to: ("module", dotted) | ("function"|"class", file, name)
Symbol = Tuple

_MAX_HOPS = 4  # re-export / inheritance hops followed before giving up


@dataclass
class ClassInfo:
    line: int = 0
    methods: Dict[str, int] = field(default_factory=dict)  # name -> def line
    bases: List[Tuple[str, ...]] = field(default_factory=list)  # dotted names


@dataclass
class FileInfo:
    """Everything the resolver needs from one parsed file."""

    path: str
    stamp: Tuple[int, int]
    functions: Dict[str, int] = field(default_factory=dict)  # top-level name -> line
    classes: Dict[str, ClassInfo] = field(default_factory=dict)
    # local name -> ("module", "a.b") | ("from", "a.b", "name")
    imports: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    # (def line, enclosing class or "", call sites)
    defs: List[Tuple[int, str, List[CallSite]]] = field(default_factory=list)


def _module_parts(path: str) -> List[str]:
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return parts


def _dotted(node: ast.AST) -> Optional[Tuple[str, ...]]:
    """`a.b.c` -> ("a", "b", "c"); None for anything that is not a plain chain."""
    names: List[str] = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    names.append(node.id)
    return tuple(reversed(names))


class _FileParser:
    """Single pass over a module collecting defs, classes, imports and calls."""

    def __init__(self, info: FileInfo) -> None:
        self.info = info
        self.package = _module_parts(info.path)
        if not info.path.endswith("__init__.py"):
            self.package = self.package[:-1]
        self.class_stack: List[str] = []
        # Per enclosing function: (def line, call sites, nested def name -> line)
        # Per enclosing function: (def line, call sites, nested def name -> line,
        # local variable names that shadow module-level names)
        self.func_stack: List[Tuple[int, List[CallSite], Dict[str, int], Set[str]]] = []
        self._handlers = {
            ast.Import: self.visit_Import,
            ast.ImportFrom: self.visit_ImportFrom,
            ast.ClassDef: self.visit_ClassDef,
            ast.FunctionDef: self.visit_FunctionDef,
            ast.AsyncFunctionDef: self.visit_FunctionDef,
            ast.Call: self.visit_Call,
        }

    def visit(self, node: ast.AST) -> None:
        handler = self._handlers.get(type(node))
        if handler:
            handler(node)
        else:
            self.generic_visit(node)

    def generic_visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    # -- imports -------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self.info.imports[alias.asname] = ("module", alias.name)
            else:
                root = alias.name.split(".")[0]
                self.info.imports.setdefault(root, ("module", root))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            base = self.package[: len(self.package) - (node.level - 1)]
            if node.level - 1 > len(self.package):
                return
            module = ".".join(base + ([node.module] if node.module else []))
        else:
            module = node.module or ""
        for alias in node.names:
            if alias.name != "*":
                self.info.imports[alias.asname or alias.name] = (
                    "from",
                    module,
                    alias.name,
                )

    # -- definitions ---------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        top_level = not self.class_stack and not self.func_stack
        if top_level:
            cls = ClassInfo(
                line=_def_line(node), bases=[d for d in map(_dotted, node.bases) if d]
            )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls.methods[item.name] = _def_line(item)
            self.info.classes[node.name] = cls
        self.class_stack.append(node.name if top_level else "")
        saved, self.func_stack = self.func_stack, []
        self.generic_visit(node)
        self.func_stack = saved
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        line = _def_line(node)
        if self.func_stack:
            self.func_stack[-1][2][node.name] = line
        elif not self.class_stack:
            self.info.functions[node.name] = line
        # Decorators and defaults run in the enclosing scope, not in the def.
        for outer in node.decorator_list + node.args.defaults + node.args.kw_defaults:
            if outer is not None:
                self.visit(outer)
        calls: List[CallSite] = []
        owner = self.class_stack[-1] if self.class_stack else ""
        self.info.defs.append((line, owner, calls))
        # Nested defs are in scope for the whole body, including earlier siblings.
        nested = {
            stmt.name: _def_line(stmt)
            for stmt in node.body
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.func_stack.append((line, calls, nested, _local_names(node)))
        for stmt in node.body:
            self.visit(stmt)
        self.func_stack.pop()

    # -- calls ---------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        if self.func_stack:
            site = self._call_site(node.func)
            if site:
                self.func_stack[-1][1].append(site)
        self.generic_visit(node)

    def _call_site(self, func: ast.AST) -> Optional[CallSite]:
        if isinstance(func, ast.Name):
            for _line, _calls, nested, local in reversed(self.func_stack):
                if func.id in nested:
                    return ("line", nested[func.id])
                if func.id in local:
                    return None  # a parameter or local variable, not a module function
            return ("name", func.id)
        if not isinstance(func, ast.Attribute):
            return None
        value = func.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "super"
        ):
            return ("super", func.attr)
        chain = _dotted(func)
        if not chain:
            return None
        if len(chain) == 2 and chain[0] in ("self", "cls"):
            return ("self", chain[1])
        return ("chain", chain)


def _local_names(func: ast.AST) -> Set[str]:
    """Parameters and names assigned in a function body (imports excluded).

    Nested function and class bodies are skipped: their locals are their own.
    """
    args = func.args
    names = {
        a.arg
        for a in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]
        if a is not None
    }
    stack = list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _def_line(node: ast.AST) -> int:
    """Upstream records a decorated def at its first decorator line."""
    decorators = getattr(node, "decorator_list", None)
    return decorators[0].lineno if decorators else node.lineno


class PythonCallGraphIndexer:
    """Resolve Python calls through imports and sync them into the code graph."""

    def __init__(self, project_path: str) -> None:
        self.project_path = Path(project_path)
        self.db_path = resolve_code_graph_db(project_path)
        self._files: Dict[str, FileInfo] = {}
        self._modules: Dict[str, Optional[str]] = {}
        self._packages: Set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_calls(self) -> Dict[str, int]:
        """Recompute Trellis's Python call edges and sync them to the DB."""
        if not self.db_path.exists():
            return {"error": "No code-graph database found"}

        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            node_at, py_paths = self._load_nodes(conn)
            parsed = self._refresh(py_paths)
            desired = self._resolve_all(node_at)
            added, removed = self._sync_edges(conn, desired)
            conn.commit()
        finally:
            conn.close()

        return {
            "files_processed": len(self._files),
            "files_parsed": parsed,
            "call_edges": len(desired),
            "call_edges_added": added,
            "call_edges_removed": removed,
        }

    def load(self) -> None:
        """Parse the indexed Python files (cached) without touching edges."""
        with closing(sqlite3.connect(str(self.db_path), timeout=30)) as conn:
            paths = {row[0] for row in conn.execute("SELECT path FROM files WHERE path LIKE '%.py'")}
        self._refresh(paths)

    def file(self, path: str) -> Optional[FileInfo]:
        return self._files.get(path)

    def module_path(self, dotted: str) -> Optional[str]:
        """File path of dotted module `dotted`, when exactly one file claims it."""
        return self._modules.get(dotted)

    def paths(self) -> List[str]:
        return list(self._files)

    def stamp(self) -> int:
        """Changes whenever any parsed file is added, removed or edited."""
        return hash(frozenset((p, f.stamp) for p, f in self._files.items()))

    def resolve(self, path: str, dotted: Tuple[str, ...]) -> Optional[Symbol]:
        """What dotted name `a.b.c` means at the top level of file `path`."""
        info = self._files.get(path)
        return self._resolve_dotted(info, dotted) if info and dotted else None

    def find_method(self, info: FileInfo, class_name: str, method: str) -> Optional[Tuple[str, int]]:
        """(path, def line) of `method` on the class or its project bases."""
        return self._find_method(info, class_name, method)

    def _refresh(self, py_paths: Set[str]) -> int:
        parsed = self._refresh_files(py_paths)
        self._modules = self._build_module_index(py_paths)
        return parsed

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_nodes(
        self, conn: sqlite3.Connection
    ) -> Tuple[Dict[Tuple[str, int], Tuple[int, str]], Set[str]]:
        """(path, start_line) -> (node id, qualified name) for Python functions."""
        node_at: Dict[Tuple[str, int], Tuple[int, str]] = {}
        py_paths: Set[str] = set()
        rows = conn.execute(
            """
            SELECT n.id, n.name, n.qualified_name, n.start_line, f.path
            FROM nodes n JOIN files f ON n.file_id = f.id
            WHERE f.path LIKE '%.py' AND n.type IN ('function', 'method')
            """
        )
        for node_id, name, qname, line, path in rows:
            node_at[(path, line)] = (node_id, qname or name)
            py_paths.add(path)
        for (path,) in conn.execute("SELECT path FROM files WHERE path LIKE '%.py'"):
            py_paths.add(path)
        return node_at, py_paths

    def _refresh_files(self, paths: Set[str]) -> int:
        """Parse new or changed files; drop removed ones. Returns files parsed."""
        for gone in set(self._files) - paths:
            del self._files[gone]
        parsed = 0
        for path in paths:
            full = self.project_path / path
            try:
                st = full.stat()
            except OSError:
                self._files.pop(path, None)
                continue
            stamp = (st.st_mtime_ns, st.st_size)
            cached = self._files.get(path)
            if cached and cached.stamp == stamp:
                continue
            info = FileInfo(path=path, stamp=stamp)
            try:
                tree = ast.parse(full.read_bytes())
                _FileParser(info).visit(tree)
            except (OSError, SyntaxError, ValueError, RecursionError):
                pass  # keep an empty entry so the file is not re-parsed each run
            self._files[path] = info
            parsed += 1
        return parsed

    def _source_roots(self) -> List[str]:
        """Package roots: the repo root plus code-graph's detected source roots."""
        roots = [""]
        roots_file = self.db_path.parent / "source-roots.json"
        try:
            data = json.loads(roots_file.read_text(encoding="utf-8"))
            roots += [r.strip("/") + "/" for r in data.get("roots", []) if r]
        except (OSError, ValueError, AttributeError):
            pass
        return roots

    def _build_module_index(self, paths: Iterable[str]) -> Dict[str, Optional[str]]:
        """Dotted module name -> file path (None when two files claim the name).

        Names are taken relative to the repo root and to each source root. A
        dotted suffix of 2+ parts (`services.billing`) is also indexed
        when unique, for projects whose package root code-graph didn't detect.
        """
        exact: Dict[str, Optional[str]] = {}
        suffix: Dict[str, Optional[str]] = {}
        roots = self._source_roots()
        for path in paths:
            for root in roots:
                if root and not path.startswith(root):
                    continue
                name = ".".join(_module_parts(path[len(root) :]))
                if name:
                    exact[name] = path if exact.get(name, path) == path else None
            parts = _module_parts(path)
            for i in range(1, len(parts) - 1):
                name = ".".join(parts[i:])
                suffix[name] = path if suffix.get(name, path) == path else None
        for name, path in suffix.items():
            exact.setdefault(name, path)
        self._packages = {
            ".".join(name.split(".")[:i])
            for name in exact
            for i in range(1, name.count(".") + 1)
        }
        return exact

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _resolve_all(
        self, node_at: Dict[Tuple[str, int], Tuple[int, str]]
    ) -> Dict[Tuple[int, int], str]:
        """(caller id, target id) -> caller qualified name."""
        desired: Dict[Tuple[int, int], str] = {}
        for path, info in self._files.items():
            for line, owner, calls in info.defs:
                caller = node_at.get((path, line))
                if not caller:
                    continue
                for site in calls:
                    target = self._resolve_site(info, owner, site)
                    if not target:
                        continue
                    target_node = node_at.get(target)
                    if target_node and target_node[0] != caller[0]:
                        desired[(caller[0], target_node[0])] = caller[1]
        return desired

    def _resolve_site(
        self, info: FileInfo, owner: str, site: CallSite
    ) -> Optional[Tuple[str, int]]:
        kind, value = site
        if kind == "line":
            return (info.path, value)
        if kind == "name":
            return self._as_function(self._lookup(info, value))
        if kind == "chain":
            return self._resolve_chain(info, value)
        if not owner:
            return None
        if kind == "self":
            return self._find_method(info, owner, value)
        for base in info.classes[owner].bases:  # super().f()
            found = self._resolve_class(info, base)
            hit = found and self._find_method(found[1], found[2], value)
            if hit:
                return hit
        return None

    def _module_file(self, module: str) -> Optional[FileInfo]:
        path = self._modules.get(module)
        return self._files.get(path) if path else None

    def _is_module(self, dotted: str) -> bool:
        return dotted in self._modules or dotted in self._packages

    def _lookup(self, info: FileInfo, name: str, hops: int = 0) -> Optional[Symbol]:
        """What `name` means at the top level of `info`, following imports."""
        if name in info.functions:
            return ("function", info, name)
        if name in info.classes:
            return ("class", info, name)
        binding = info.imports.get(name)
        if not binding or hops > _MAX_HOPS:
            return None
        if binding[0] == "module":
            return ("module", binding[1]) if self._is_module(binding[1]) else None
        return self._member(("module", binding[1]), binding[2], hops + 1)

    def _member(self, symbol: Symbol, attr: str, hops: int = 0) -> Optional[Symbol]:
        """`module.attr`: a name the module defines or re-exports, else a submodule."""
        if symbol[0] != "module":
            return None
        dotted = symbol[1]
        source = self._module_file(dotted)
        found = source and self._lookup(source, attr, hops)
        if found:
            return found
        sub = f"{dotted}.{attr}" if dotted else attr
        return ("module", sub) if self._is_module(sub) else None

    def _resolve_dotted(self, info: FileInfo, dotted: Tuple[str, ...]) -> Optional[Symbol]:
        symbol = self._lookup(info, dotted[0])
        for attr in dotted[1:]:
            if not symbol or symbol[0] != "module":
                return None
            symbol = self._member(symbol, attr)
        return symbol

    def _resolve_class(self, info: FileInfo, dotted: Tuple[str, ...]) -> Optional[Symbol]:
        symbol = self._resolve_dotted(info, dotted)
        return symbol if symbol and symbol[0] == "class" else None

    def _resolve_chain(
        self, info: FileInfo, chain: Tuple[str, ...]
    ) -> Optional[Tuple[str, int]]:
        """`a.b.f()`: a function in module a.b, or method f of class a.b."""
        owner = self._resolve_dotted(info, chain[:-1])
        if not owner:
            return None
        if owner[0] == "class":
            return self._find_method(owner[1], owner[2], chain[-1])
        return self._as_function(self._member(owner, chain[-1]))

    @staticmethod
    def _as_function(symbol: Optional[Symbol]) -> Optional[Tuple[str, int]]:
        if symbol and symbol[0] == "function":
            return (symbol[1].path, symbol[1].functions[symbol[2]])
        return None

    def _find_method(
        self, info: FileInfo, class_name: str, method: str, hops: int = 0
    ) -> Optional[Tuple[str, int]]:
        cls = info.classes.get(class_name)
        if not cls or hops > _MAX_HOPS:
            return None
        if method in cls.methods:
            return (info.path, cls.methods[method])
        for base in cls.bases:
            found = self._resolve_class(info, base)
            hit = found and self._find_method(found[1], found[2], method, hops + 1)
            if hit:
                return hit
        return None

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def _sync_edges(
        self, conn: sqlite3.Connection, desired: Dict[Tuple[int, int], str]
    ) -> Tuple[int, int]:
        """Make the DB's Trellis-owned Python call edges equal `desired`."""
        owned: Dict[Tuple[int, int], List[int]] = {}
        rows = conn.execute(
            """
            SELECT e.id, e.source_id, e.target_id
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE e.relation = 'calls' AND e.metadata LIKE '{"caller"%'
              AND f.path LIKE '%.py'
            """
        )
        for edge_id, source, target in rows:
            owned.setdefault((source, target), []).append(edge_id)

        confident = confident_filter(conn, alias="")
        upstream: Set[Tuple[int, int]] = set(
            conn.execute(
                f"""
                SELECT source_id, target_id FROM edges
                WHERE relation = 'calls'
                  AND (metadata IS NULL OR metadata NOT LIKE '{{"caller"%')
                  {confident}
                """
            )
        )

        stale = [
            edge_id
            for pair, ids in owned.items()
            for edge_id in (ids if pair not in desired or pair in upstream else ids[1:])
        ]
        conn.executemany("DELETE FROM edges WHERE id = ?", ((i,) for i in stale))

        new_rows = [
            (s, t, json.dumps({"caller": qname}))
            for (s, t), qname in desired.items()
            if (s, t) not in owned and (s, t) not in upstream
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO edges (source_id, target_id, relation, metadata) "
            "VALUES (?, ?, 'calls', ?)",
            new_rows,
        )
        return len(new_rows), len(stale)


if __name__ == "__main__":
    import sys

    project = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps(PythonCallGraphIndexer(project).index_calls(), indent=2))
