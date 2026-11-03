"""Read-side queries over the code-graph index shared by the impact, function
and feature tools: symbol lookup and caller lookup by node id."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .utils import has_column, is_test_path

FUNC_TYPES = ("function", "method")
SYMBOL_TYPES = ("function", "method", "class")
_CHUNK = 500  # stay well under SQLite's bound-parameter limit


def connect(db_path: Path) -> "closing[sqlite3.Connection]":
    """Short-lived connection, closed by the `with` block."""
    return closing(sqlite3.connect(str(db_path), timeout=10))


def split_symbol(spec: str) -> Tuple[str, str]:
    """'dir/file.py:Class.method' -> ('dir/file.py', 'Class.method').

    Also accepts pytest-style 'file.py::name'. A bare name has no file part.
    """
    spec = spec.strip().replace("\\", "/")
    for sep in ("::", ":"):
        if sep in spec:
            file_part, _, name = spec.rpartition(sep)
            if name and "/" not in name:
                return file_part.strip(), name.strip()
    return "", spec


def path_matches(path: str, file_part: str) -> bool:
    """Does indexed `path` match a user-given file (full, suffix or absolute)?"""
    return _path_matches(path, file_part)


def _path_matches(path: str, file_part: str) -> bool:
    while file_part.startswith("./"):
        file_part = file_part[2:]
    return (
        path == file_part
        or path.endswith("/" + file_part)
        or file_part.endswith("/" + path)
    )


def _module_matches(path: str, module: str) -> bool:
    """Does `path` hold dotted `module` (e.g. services.billing)?"""
    stem = module.replace(".", "/")
    return any(_path_matches(path, stem + ext) for ext in (".py", "/__init__.py"))


def _node_rows(
    conn: sqlite3.Connection, name: str, types: Tuple[str, ...]
) -> List[Dict]:
    signature = "n.signature" if has_column(conn, "nodes", "signature") else "NULL"
    column = "n.qualified_name" if "." in name else "n.name"
    placeholders = ",".join("?" * len(types))
    rows = conn.execute(
        f"""
        SELECT n.id, n.name, n.qualified_name, f.path, n.start_line, n.end_line,
               n.type, {signature}
        FROM nodes n JOIN files f ON n.file_id = f.id
        WHERE {column} = ? AND n.type IN ({placeholders})
        """,
        (name, *types),
    )
    return [
        {
            "id": r[0],
            "name": r[1],
            "qualified_name": r[2] or r[1],
            "file_path": r[3],
            "line": r[4],
            "end_line": r[5],
            "type": r[6],
            "signature": r[7] or "",
        }
        for r in rows
    ]


def find_symbol(
    conn: sqlite3.Connection, spec: str, types: Tuple[str, ...] = FUNC_TYPES
) -> List[Dict]:
    """All definitions matching `spec`, best first.

    Forms: 'name', 'Class.method', 'path/file.py:name', 'file.py:Class.method',
    'package.module.name'. Non-test definitions rank before test ones.
    """
    file_part, name = split_symbol(spec)
    if not name:
        return []
    matches = _node_rows(conn, name, types)
    if file_part:
        matches = [m for m in matches if _path_matches(m["file_path"], file_part)]
    elif not matches and "." in name:
        parts = name.split(".")
        for size in (1, 2):  # module.func, then module.Class.method
            if len(parts) <= size:
                break
            module, symbol = ".".join(parts[:-size]), ".".join(parts[-size:])
            matches = [
                m
                for m in _node_rows(conn, symbol, types)
                if _module_matches(m["file_path"], module)
            ]
            if matches:
                break
    return sorted(
        matches,
        key=lambda m: (
            is_test_path(m["file_path"]),
            m["qualified_name"] != name,
            len(m["file_path"]),
            m["line"],
        ),
    )


def describe(node: Dict) -> str:
    """'path/file.py:Qualified.name' — the form find_symbol accepts back."""
    return f"{node['file_path']}:{node['qualified_name']}"


def confident_filter(conn: sqlite3.Connection, alias: str = "e") -> str:
    """SQL 'AND ...' clause excluding upstream 'ambiguous' by-name edges.

    Empty for older indexes, which have no edges.confidence column.
    """
    if has_column(conn, "edges", "confidence"):
        return f"AND {alias}.confidence != 'ambiguous'" if alias else "AND confidence != 'ambiguous'"
    return ""


def _confident(conn: sqlite3.Connection) -> str:
    """SQL expression: 1 when an edge is not an upstream 'ambiguous' by-name guess."""
    if has_column(conn, "edges", "confidence"):
        return "MAX(CASE WHEN e.confidence = 'ambiguous' THEN 0 ELSE 1 END)"
    return "1"


def callers_of(conn: sqlite3.Connection, node_id: int) -> Dict[str, List[Dict]]:
    """Functions with a 'calls' edge into `node_id`, split three ways.

    callers: confident edges from non-test code; test_callers: confident
    edges from test files; ambiguous: only upstream by-name guesses, which
    may point at a same-named function elsewhere.
    """
    rows = conn.execute(
        f"""
        SELECT s.id, s.name, s.qualified_name, f.path, s.start_line, s.type,
               {_confident(conn)}
        FROM edges e
        JOIN nodes s ON e.source_id = s.id
        JOIN files f ON s.file_id = f.id
        WHERE e.relation = 'calls' AND e.target_id = ? AND s.id != ?
          AND s.type IN ('function', 'method')
        GROUP BY s.id
        ORDER BY f.path, s.start_line
        """,
        (node_id, node_id),
    )
    split: Dict[str, List[Dict]] = {"callers": [], "test_callers": [], "ambiguous": []}
    for sid, name, qname, path, line, kind, confident in rows:
        entry = {
            "node_id": sid,
            "name": name,
            "qualified_name": qname or name,
            "file_path": path,
            "line": line,
            "type": kind,
        }
        if not confident:
            split["ambiguous"].append(entry)
        elif is_test_path(path):
            split["test_callers"].append(entry)
        else:
            split["callers"].append(entry)
    if split["ambiguous"]:
        resolved = _resolved_elsewhere(conn, node_id, [c["node_id"] for c in split["ambiguous"]])
        split["ambiguous"] = [c for c in split["ambiguous"] if c["node_id"] not in resolved]
    return split


def callees_of(conn: sqlite3.Connection, node_id: int, limit: int = 50) -> List[Dict]:
    """Project functions/methods `node_id` calls through confident edges.

    Calls into external code (no project definition) and upstream by-name
    guesses are left out, and so are test-file targets of a non-test caller:
    production code does not call into tests, so such an edge is a
    mis-resolved method call (a Django `qs.first()` bound to `SomeTests.first`).
    """
    source = conn.execute(
        "SELECT f.path FROM nodes n JOIN files f ON n.file_id = f.id WHERE n.id = ?", (node_id,)
    ).fetchone()
    from_test = bool(source) and is_test_path(source[0])
    rows = conn.execute(
        f"""
        SELECT DISTINCT t.id, t.name, t.qualified_name, f.path, t.start_line, t.type
        FROM edges e
        JOIN nodes t ON e.target_id = t.id
        JOIN files f ON t.file_id = f.id
        WHERE e.relation = 'calls' AND e.source_id = ? AND t.id != ? {confident_filter(conn)}
          AND t.type IN ('function', 'method', 'class')
        ORDER BY f.path, t.start_line
        """,
        (node_id, node_id),
    )
    found = []
    for r in rows:
        if not from_test and is_test_path(r[3]):
            continue
        found.append(
            {"node_id": r[0], "name": r[1], "qualified_name": r[2] or r[1], "file_path": r[3], "line": r[4], "type": r[5]}
        )
        if len(found) >= limit:
            break
    return found


def _resolved_elsewhere(conn: sqlite3.Connection, node_id: int, sources: List[int]) -> set:
    """Sources whose call to this name is confidently bound to another definition.

    Their by-name edge here is the other half of that guess, so it is noise.
    """
    found: set = set()
    for start in range(0, len(sources), _CHUNK):
        chunk = sources[start : start + _CHUNK]
        found.update(
            row[0]
            for row in conn.execute(
                f"""
                SELECT DISTINCT e.source_id
                FROM edges e
                JOIN nodes t ON e.target_id = t.id
                WHERE e.relation = 'calls' {confident_filter(conn)}
                  AND t.name = (SELECT name FROM nodes WHERE id = ?) AND t.id != ?
                  AND e.source_id IN ({",".join("?" * len(chunk))})
                """,
                (node_id, node_id, *chunk),
            )
        )
    return found


def caller_counts(conn: sqlite3.Connection, node_ids: Iterable[int]) -> Dict[int, int]:
    """node id -> number of distinct confident, non-test callers."""
    ids = list(node_ids)
    counts: Dict[int, int] = {}
    confident = confident_filter(conn)
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        rows = conn.execute(
            f"""
            SELECT e.target_id, f.path, COUNT(DISTINCT e.source_id)
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE e.relation = 'calls' {confident}
              AND e.target_id IN ({",".join("?" * len(chunk))})
              AND e.source_id != e.target_id
              AND s.type IN ('function', 'method')
            GROUP BY e.target_id, f.path
            """,
            chunk,
        )
        for target, path, n in rows:
            if not is_test_path(path):
                counts[target] = counts.get(target, 0) + n
    return counts


def named_caller_count(conn: sqlite3.Connection, path: str, name: str, kind: str) -> int:
    """Distinct confident, non-test callers of every `name` of type `kind` in `path`.

    For engine rows that name a symbol by file and name only (centrality).
    """
    rows = conn.execute(
        f"""
        SELECT DISTINCT e.source_id, sf.path
        FROM nodes t
        JOIN files tf ON t.file_id = tf.id
        JOIN edges e ON e.target_id = t.id AND e.relation = 'calls' {confident_filter(conn)}
        JOIN nodes s ON e.source_id = s.id
        JOIN files sf ON s.file_id = sf.id
        WHERE tf.path = ? AND t.name = ? AND t.type = ?
          AND e.source_id != t.id AND s.type IN ('function', 'method')
        """,
        (path, name, kind),
    )
    return sum(1 for _, source_path in rows if not is_test_path(source_path))


def callers_of_all(conn: sqlite3.Connection, node_ids: List[int]) -> Dict[str, List[Dict]]:
    """callers_of merged over several targets (a class and its methods)."""
    merged: Dict[str, Dict[int, Dict]] = {"callers": {}, "test_callers": {}, "ambiguous": {}}
    targets = set(node_ids)
    for node_id in node_ids:
        for kind, entries in callers_of(conn, node_id).items():
            for entry in entries:
                if entry["node_id"] not in targets:
                    merged[kind].setdefault(entry["node_id"], entry)
    confident = set(merged["callers"]) | set(merged["test_callers"])
    return {
        kind: sorted(
            (e for e in entries.values() if kind != "ambiguous" or e["node_id"] not in confident),
            key=lambda e: (e["file_path"], e["line"]),
        )
        for kind, entries in merged.items()
    }


def class_methods(conn: sqlite3.Connection, class_node: Dict) -> List[int]:
    """Ids of the methods defined in a class node's line range."""
    rows = conn.execute(
        """
        SELECT n.id FROM nodes n JOIN files f ON n.file_id = f.id
        WHERE f.path = ? AND n.type = 'method' AND n.start_line > ? AND n.start_line <= ?
        """,
        (class_node["file_path"], class_node["line"], class_node["end_line"]),
    )
    return [row[0] for row in rows]


def indirect_callers(
    conn: sqlite3.Connection, start_ids: Iterable[int], depth: int = 3, limit: int = 200
) -> List[Dict]:
    """Callers of callers (levels 2..depth), confident non-test edges only."""
    confident = confident_filter(conn)
    seen = set(start_ids)
    frontier = list(seen)
    found: List[Dict] = []
    for level in range(1, max(depth, 1) + 1):
        next_frontier: List[int] = []
        for start in range(0, len(frontier), _CHUNK):
            chunk = frontier[start : start + _CHUNK]
            rows = conn.execute(
                f"""
                SELECT DISTINCT s.id, s.name, s.qualified_name, f.path, s.start_line
                FROM edges e JOIN nodes s ON e.source_id = s.id JOIN files f ON s.file_id = f.id
                WHERE e.relation = 'calls' {confident}
                  AND e.target_id IN ({",".join("?" * len(chunk))})
                  AND s.type IN ('function', 'method')
                """,
                chunk,
            )
            for sid, name, qname, path, line in rows:
                if sid in seen or is_test_path(path):
                    continue
                seen.add(sid)
                next_frontier.append(sid)
                if level > 1 and len(found) < limit:
                    found.append(
                        {"depth": level, "qualified_name": qname or name, "file_path": path, "line": line}
                    )
        frontier = next_frontier
        if not frontier:
            break
    return found


def code_content(conn: sqlite3.Connection, node_id: int) -> Optional[str]:
    if not has_column(conn, "nodes", "code_content"):
        return None
    row = conn.execute("SELECT code_content FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return row[0] if row else None


def functions_in_file(conn: sqlite3.Connection, path: str) -> List[Dict]:
    rows = conn.execute(
        """
        SELECT n.id, n.name, n.qualified_name, n.start_line, n.type
        FROM nodes n JOIN files f ON n.file_id = f.id
        WHERE f.path = ? AND n.type IN ('function', 'method')
        ORDER BY n.start_line
        """,
        (path,),
    )
    return [
        {
            "node_id": r[0],
            "name": r[1],
            "qualified_name": r[2] or r[1],
            "file_path": path,
            "line": r[3],
            "type": r[4],
        }
        for r in rows
    ]


def downstream_calls(
    conn: sqlite3.Connection, start_ids: Iterable[int], depth: int = 3, limit: int = 60
) -> List[Dict]:
    """Breadth-first walk of confident 'calls' edges out of `start_ids`.

    Returns one entry per edge: depth, caller and callee as 'file:qualified'.
    Test code and upstream 'ambiguous' guesses are skipped.
    """
    confident = confident_filter(conn)
    seen = set(start_ids)
    frontier = list(seen)
    names: Dict[int, str] = {}
    edges: List[Dict] = []
    for level in range(1, max(depth, 0) + 1):
        if not frontier or len(edges) >= limit:
            break
        next_frontier: List[int] = []
        for start in range(0, len(frontier), _CHUNK):
            chunk = frontier[start : start + _CHUNK]
            rows = conn.execute(
                f"""
                SELECT DISTINCT e.source_id, sf.path, s.qualified_name, s.name,
                       e.target_id, tf.path, t.qualified_name, t.name, t.start_line
                FROM edges e
                JOIN nodes s ON e.source_id = s.id JOIN files sf ON s.file_id = sf.id
                JOIN nodes t ON e.target_id = t.id JOIN files tf ON t.file_id = tf.id
                WHERE e.relation = 'calls' {confident}
                  AND e.source_id IN ({",".join("?" * len(chunk))})
                  AND t.type IN ('function', 'method')
                ORDER BY e.source_id, tf.path, t.start_line
                """,
                chunk,
            )
            for sid, spath, sq, sn, tid, tpath, tq, tn, tline in rows:
                if is_test_path(tpath) or len(edges) >= limit:
                    continue
                names.setdefault(sid, f"{spath}:{sq or sn}")
                edges.append(
                    {"depth": level, "from": names[sid], "to": f"{tpath}:{tq or tn}", "line": tline}
                )
                if tid not in seen:
                    seen.add(tid)
                    next_frontier.append(tid)
        frontier = next_frontier
    return edges


def hotspots(conn: sqlite3.Connection, limit: int = 20) -> List[Dict]:
    """Non-test functions with the most distinct confident non-test users.

    A user is a function calling it or a module importing it.
    """
    confident = confident_filter(conn)
    rows = conn.execute(
        f"""
        SELECT t.id, t.name, t.qualified_name, tf.path, t.start_line,
               sf.path, COUNT(DISTINCT e.source_id)
        FROM edges e
        JOIN nodes t ON e.target_id = t.id JOIN files tf ON t.file_id = tf.id
        JOIN nodes s ON e.source_id = s.id JOIN files sf ON s.file_id = sf.id
        WHERE e.relation IN ('calls', 'imports') {confident}
          AND t.type IN ('function', 'method')
          AND s.type IN ('function', 'method', 'module')
          AND e.source_id != e.target_id
        GROUP BY t.id, sf.path
        """
    )
    totals: Dict[int, Dict] = {}
    for tid, name, qname, path, line, source_path, n in rows:
        if is_test_path(path) or is_test_path(source_path):
            continue
        entry = totals.setdefault(
            tid,
            {"name": name, "qualified_name": qname or name, "file_path": path, "line": line, "used_by": 0},
        )
        entry["used_by"] += n
    return sorted(totals.values(), key=lambda e: -e["used_by"])[:limit]
