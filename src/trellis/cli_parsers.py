"""Parsers for code-graph-mcp CLI text output.

The Rust MCP server exposes only a subset of tools via the MCP protocol; many
analysis commands (impact, refs, deps, dead-code, map, overview) are CLI-only.
This module turns their stdout into structured Python dicts/lists so the Python
bridge can expose a consistent API.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _parse_location(location: str) -> Dict[str, Any]:
    """Parse strings like 'path/to/file.js:123' or 'path/to/file.js:10-15'."""
    if ":" not in location:
        return {"file_path": location}

    file_path, rest = location.rsplit(":", 1)
    if "-" in rest:
        try:
            start, end = rest.split("-", 1)
            return {
                "file_path": file_path,
                "start_line": int(start),
                "end_line": int(end),
            }
        except ValueError:
            pass
    try:
        return {"file_path": file_path, "start_line": int(rest)}
    except ValueError:
        return {"file_path": location}


def parse_impact(stdout: str) -> Dict[str, Any]:
    """Parse `code-graph-mcp impact <symbol>` output."""
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}

    header = lines[0]
    match = re.match(r"Impact:\s*(.+?)\s*—\s*Risk:\s*(\w+)", header)
    if not match:
        return {"text": stdout}

    symbol, risk = match.groups()
    result: Dict[str, Any] = {
        "symbol": symbol.strip(),
        "risk": risk.strip(),
        "callers": [],
    }

    summary_match = re.search(
        r"(\d+)\s+direct\s+callers?,\s+(\d+)\s+total,\s+(\d+)\s+files?,\s+(\d+)\s+routes?",
        stdout,
    )
    if summary_match:
        result["direct_callers"] = int(summary_match.group(1))
        result["total_callers"] = int(summary_match.group(2))
        result["files"] = int(summary_match.group(3))
        result["routes"] = int(summary_match.group(4))

    caller_re = re.compile(r"^\s+((?:\s{2,})?)(\S.*?)\s+\(([^)]+)\)\s+(.+)$")
    stack: List[Dict[str, Any]] = []
    for line in lines[2:]:
        if line.strip().startswith("Callers:"):
            continue
        m = caller_re.match(line)
        if not m:
            continue
        indent = len(m.group(1)) // 2
        name = m.group(2).strip()
        kind = m.group(3).strip()
        location = m.group(4).strip()
        node = {
            "name": name,
            "kind": kind,
            "location": _parse_location(location),
            "children": [],
        }
        if indent == 0 or not stack:
            result["callers"].append(node)
            stack = [node]
        else:
            parent = stack[min(indent - 1, len(stack) - 1)]
            parent["children"].append(node)
            stack = stack[:indent] + [node]

    return result


def parse_callgraph(stdout: str) -> Dict[str, Any]:
    """Parse `code-graph-mcp callgraph <symbol>` output."""
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}

    root_match = re.match(r"^(\S+)\s+\(([^)]+)\)", lines[0])
    if not root_match:
        return {"text": stdout}

    root_name = root_match.group(1)
    root_location = _parse_location(root_match.group(2))
    result: Dict[str, Any] = {
        "symbol": root_name,
        "location": root_location,
        "nodes": [],
    }

    # Each line: '  ← called by: name (location) [kind]' or '    ← called by: ...'
    edge_re = re.compile(r"^\s+←\s+called\s+by:\s+(.+?)\s+\(([^)]+)\)\s+\[([^\]]+)\]")
    for line in lines[1:]:
        m = edge_re.match(line)
        if not m:
            continue
        result["nodes"].append(
            {
                "name": m.group(1).strip(),
                "location": _parse_location(m.group(2)),
                "kind": m.group(3),
            }
        )

    return result


def parse_refs(stdout: str) -> List[Dict[str, Any]]:
    """Parse `code-graph-mcp refs <symbol>` output."""
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    refs: List[Dict[str, Any]] = []
    ref_re = re.compile(r"^\s+\[(\w+)\]\s+(.+?)\s+\(([^)]+)\)")
    for line in lines:
        m = ref_re.match(line)
        if not m:
            continue
        refs.append(
            {
                "relation": m.group(1),
                "name": m.group(2).strip(),
                "location": _parse_location(m.group(3)),
            }
        )
    return refs


def parse_deps(stdout: str) -> Dict[str, Any]:
    """Parse `code-graph-mcp deps <file>` output."""
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}

    result: Dict[str, Any] = {
        "file_path": lines[0].strip(),
        "depends_on": [],
        "depended_by": [],
    }
    current = None
    dep_re = re.compile(
        r"^\s+(\S.*?\.(?:js|ts|jsx|tsx|py|rs|go|java|cpp|c|h|cs))\s*(?:\((\d+)\s+symbols?\)|\(depth\s+(\d+)\))"
    )

    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("Depends on:"):
            current = "depends_on"
            continue
        if stripped.startswith("Depended by:"):
            current = "depended_by"
            continue

        m = dep_re.match(line)
        if m and current:
            entry: Dict[str, Any] = {"file_path": m.group(1).strip()}
            if m.group(2):
                entry["symbols"] = int(m.group(2))
            if m.group(3):
                entry["depth"] = int(m.group(3))
            result[current].append(entry)

    return result


def parse_dead_code(stdout: str) -> List[Dict[str, Any]]:
    """Parse `code-graph-mcp dead-code [path]` output."""
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    results: List[Dict[str, Any]] = []
    header_re = re.compile(
        r"Dead\s+code:\s+(\d+)\s+results\s+\((\d+)\s+orphan,\s+(\d+)\s+exported-unused\)"
    )
    orphan_re = re.compile(r"^\s+(\w+)\s+(.+?)\s+(.+?\.\w+):(\d+)\s+\((\d+)\s+lines\)")

    for line in lines:
        if header_re.match(line):
            continue
        m = orphan_re.match(line)
        if m:
            results.append(
                {
                    "category": "orphan",
                    "kind": m.group(1),
                    "name": m.group(2).strip(),
                    "location": _parse_location(f"{m.group(3)}:{m.group(4)}"),
                    "lines": int(m.group(5)),
                }
            )

    return results


def parse_map(stdout: str) -> Dict[str, Any]:
    """Parse `code-graph-mcp map` output."""
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    result: Dict[str, Any] = {"modules": [], "dependencies": []}
    current = None

    module_re = re.compile(
        r"^(\S.*?\w)\s+\((\d+)\s+files?,\s+(\d+)\s+symbols?(?:,\s+(.+?))?\)"
    )
    dep_re = re.compile(r"^\s+(\S.*?\w)\s+→\s+(\S.*?\w)\s+\((\d+)\s+imports?\)")
    symbol_re = re.compile(r"^\s+([\w_,\s]+)")

    current_module: Optional[Dict[str, Any]] = None
    for line in lines:
        stripped = line.strip()
        if stripped == "Modules:":
            current = "modules"
            continue
        if stripped == "Dependencies:":
            current = "dependencies"
            current_module = None
            continue

        if current == "modules":
            m = module_re.match(line)
            if m:
                current_module = {
                    "path": m.group(1).strip(),
                    "files": int(m.group(2)),
                    "symbols": int(m.group(3)),
                    "language": m.group(4) or "",
                    "symbol_names": [],
                }
                result["modules"].append(current_module)
            elif current_module and symbol_re.match(line):
                names = [n.strip() for n in stripped.split(",") if n.strip()]
                current_module["symbol_names"].extend(names)

        elif current == "dependencies":
            m = dep_re.match(line)
            if m:
                result["dependencies"].append(
                    {
                        "from": m.group(1).strip(),
                        "to": m.group(2).strip(),
                        "imports": int(m.group(3)),
                    }
                )

    return result


def parse_overview(stdout: str) -> Dict[str, Any]:
    """Parse `code-graph-mcp overview <path>` output."""
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    result: Dict[str, Any] = {"files": {}}
    current_file: Optional[str] = None
    file_re = re.compile(r"^(\S.*?\.(?:js|ts|jsx|tsx|py|rs|go|java|cpp|c|h|cs|md))")
    symbol_re = re.compile(r"^\s+(\w+):\s+(.+?)(?:\s+\((\d+)×\))?$")

    for line in lines:
        m = file_re.match(line)
        if m:
            current_file = m.group(1).strip()
            result["files"][current_file] = []
            continue

        if current_file:
            sm = symbol_re.match(line)
            if sm:
                entry: Dict[str, Any] = {
                    "kind": sm.group(1),
                    "names": [n.strip() for n in sm.group(2).split(",") if n.strip()],
                }
                if sm.group(3):
                    entry["count"] = int(sm.group(3))
                result["files"][current_file].append(entry)

    return result


def parse_show(stdout: str) -> Dict[str, Any]:
    """Parse `code-graph-mcp show <symbol>` output.

    Expected header format:
        fn name  path:start-end  ((signature)) -> type
        class Name  path:line
    """
    lines = [line.rstrip() for line in stdout.splitlines()]
    if not lines:
        return {}

    header = lines[0]
    # kind, name, location, optional signature
    header_re = re.compile(r"^(\w+)\s+(\S+)\s+(.+?\.\w+:\d+(?:-\d+)?)\s*(\(.*)?$")
    m = header_re.match(header)
    if not m:
        return {"source": stdout}

    location = _parse_location(m.group(3))
    return {
        "kind": m.group(1),
        "name": m.group(2),
        "location": location,
        "file_path": location.get("file_path", ""),
        "start_line": location.get("start_line"),
        "end_line": location.get("end_line"),
        "signature": (m.group(4) or "").strip(),
        "source": "\n".join(lines[1:]).strip(),
    }


def parse_trace(stdout: str) -> Dict[str, Any]:
    """Parse `code-graph-mcp trace <route>` output."""
    return {"trace": stdout.strip()}


def parse_ast_search(stdout: str) -> List[Dict[str, Any]]:
    """Parse `code-graph-mcp ast-search <query>` output."""
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    results: List[Dict[str, Any]] = []
    # Lines look like: "fn Component.constructor  apps/.../component.js:9-21  ((name, graphics))"
    line_re = re.compile(r"^(\w+)\s+(\S+)\s+\s+(\S+?\.\w+):(\d+)(?:-(\d+))?\s*(.*)$")
    for line in lines:
        m = line_re.match(line)
        if m:
            results.append(
                {
                    "kind": m.group(1),
                    "name": m.group(2),
                    "file_path": m.group(3),
                    "start_line": int(m.group(4)),
                    "end_line": int(m.group(5)) if m.group(5) else int(m.group(4)),
                    "signature": m.group(6).strip(),
                }
            )
    return results
