"""Trellis MCP server - graph-based code analysis and impact detection.

This module exposes both HTTP routes (for the visualizer) and MCP tools
(for AI coding agents). Uses code-graph-mcp via CodeGraphBridge.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.trellis import CodeGraphBridge

from spec_manager import SpecManager

# Load environment variables from .env file in development only.
# In the bundled release, environment defaults are set by src/trellis/launcher.py.
if not getattr(sys, "frozen", False):
    load_dotenv(Path(__file__).with_name(".env"))

# Ensure banner is suppressed via env var
if os.environ.get("FASTMCP_SHOW_SERVER_BANNER") is None:
    os.environ["FASTMCP_SHOW_SERVER_BANNER"] = "false"
if os.environ.get("FASTMCP_LOG_LEVEL") is None:
    os.environ["FASTMCP_LOG_LEVEL"] = "ERROR"

VERSION = "0.2.0"

# ------------------------------------------------------------------
# Response guards (token/perf protection for coding agents)
# ------------------------------------------------------------------

# Hard budget for a single MCP tool response. Oversized payloads are rejected
# with guidance instead of being dumped into the agent's context window.
MAX_RESPONSE_CHARS = int(os.environ.get("TRELLIS_MAX_RESPONSE_CHARS", "50000"))

# Per-field truncation limits for potentially huge values.
MAX_CODE_CONTENT_CHARS = 4000
MAX_NOTE_CONTENT_CHARS = 2000

# trellis_analyze_diff is disabled by default: broad diffs run per-function
# impact analysis (subprocess per function) and time out. Opt in explicitly.
DIFF_ANALYSIS_ENABLED = os.environ.get("TRELLIS_ENABLE_DIFF_ANALYSIS", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
MAX_DIFF_CHARS = int(os.environ.get("TRELLIS_MAX_DIFF_CHARS", "200000"))
MAX_DIFF_FILES = int(os.environ.get("TRELLIS_MAX_DIFF_FILES", "50"))
MAX_DIFF_FUNCTIONS = int(os.environ.get("TRELLIS_MAX_DIFF_FUNCTIONS", "25"))


def _dump(payload: Any) -> str:
    """Serialize a tool response as compact JSON with a hard size budget.

    Compact separators avoid the ~30-50% token overhead of pretty-printing.
    Payloads beyond MAX_RESPONSE_CHARS are replaced with an actionable error
    so agents narrow the query instead of receiving a context-filling dump.
    """
    text = json.dumps(payload, separators=(",", ":"), default=str)
    if len(text) > MAX_RESPONSE_CHARS:
        return json.dumps(
            {
                "error": "response_too_large",
                "size_chars": len(text),
                "limit_chars": MAX_RESPONSE_CHARS,
                "hint": (
                    "Narrow the query: use a specific module_path, feature_name, "
                    "or function_path, or lower result limits. Raise "
                    "TRELLIS_MAX_RESPONSE_CHARS only if you really need more."
                ),
            }
        )
    return text


def _truncate(text: str, limit: int) -> str:
    """Truncate a string field, appending a marker when truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text)} chars total]"


def _project_md_status(project_path) -> Dict[str, str]:
    """Report whether the repo root has a project.md feature spec.

    Feature-level analysis (feature_info, trace_path, feature impacts) depends
    on project.md, so agents must create it when missing.
    """
    path = Path(project_path) / "project.md"
    if path.exists():
        return {"status": "ok", "path": str(path)}
    return {
        "status": "missing",
        "path": str(path),
        "required_action": (
            "project.md is missing. You MUST create it before relying on "
            "feature-level analysis: add a '## Feature: <Name>' section per "
            "feature with Description, Decisions, Files (glob patterns), and "
            "Dependencies. See the trellis-mcp skill project.md template."
        ),
    }


_SERVER_INSTRUCTIONS = """\
Trellis: graph-based code analysis and knowledge management for this workspace.

Getting started:
1. Sync once per project: trellis_sync(project_id="<repo name or path>", repo_path="<repo root path>"). Check the project_md field in the response. The index then stays fresh automatically — the graph server watches the repo and re-indexes edited files on its own.
2. If project_md is "missing", create <repo root>/project.md BEFORE any feature-level work. Format (parsed strictly): one "## Feature: <Name>" section per feature, containing a plain-text description paragraph, then "### Decisions" ("- DEC-001: decision (because: rationale)" with nested "- Constraint: ..."), "### Files" (glob patterns, one per line, e.g. src/auth/**), and "### Dependencies" ("- Feature: <Other Feature>"). Other layouts (tables, "- **Description**:" bullets) are ignored.
3. Explore with trellis_list_modules and trellis_search_code; inspect symbols with trellis_get_function; understand features with trellis_feature_info.
4. Before modifying any function, run trellis_analyze_impact on it. The graph reflects your edits automatically — just re-analyze. Run trellis_sync again only after large external changes (big pulls, branch switches) or if results look stale.
5. Document decisions and feature architecture with trellis_create_note (wiki links [[Note]], code mentions @function).

The full skill document — complete workflow, project.md template, and tool reference — is available as the MCP resource "trellis://skill". Fetch it if the trellis-mcp skill is not installed in your agent.
"""

# ------------------------------------------------------------------
# HTTP hardening (CSRF / DNS-rebinding defense for the local server)
# ------------------------------------------------------------------
# The HTTP transport serves the visualizer UI, REST-ish custom routes, and
# the MCP endpoint on 127.0.0.1. Browser-based attackers (malicious websites)
# can reach localhost servers via simple requests and DNS rebinding, so every
# HTTP request is checked by _LocalHttpGuard (applied in the transport branch
# of main()):
#   1. Host header must be a local hostname — blocks DNS rebinding, and
#      guarantees the token below is only ever served to a local page.
#   2. Token: everything except "/" and "/health" must carry the launch
#      token. TRELLIS_API_KEY is honored as a stable token for MCP-over-HTTP
#      clients; otherwise a random per-launch token is generated and embedded
#      into the served visualizer page (never readable cross-origin, since
#      no CORS headers are ever emitted).
#   3. If an Origin header is present it must be a local origin — blocks
#      cross-site form/fetch CSRF on mutating requests.

_HTTP_TOKEN = os.environ.get("TRELLIS_API_KEY", "").strip() or secrets.token_hex(16)

_LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
_LOCAL_HOSTNAMES.add(os.environ.get("TRELLIS_HOST", "127.0.0.1").split(":")[0].strip("[]").lower())


def _hostname_of(host_or_origin: str) -> str:
    """Extract a lowercase hostname from a Host header value or an Origin URL."""
    value = (host_or_origin or "").strip()
    if "://" in value:  # Origin header is a URL
        value = urlsplit(value).netloc
    if value.startswith("["):
        return value[1 : value.index("]")].lower()
    return value.rsplit(":", 1)[0].lower()


class _LocalHttpGuard:
    """Pure-ASGI guard; safe for streaming responses (unlike BaseHTTPMiddleware)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        path = scope.get("path", "")

        if _hostname_of(headers.get("host", "")) not in _LOCAL_HOSTNAMES:
            await self._reject(send, 403, "Forbidden: untrusted Host header")
            return

        if path not in ("/", "/health") and not path.startswith("/vendor/"):
            auth = headers.get("authorization", "")
            token_ok = (
                auth.startswith("Bearer ")
                and hmac.compare_digest(auth[7:], _HTTP_TOKEN)
            ) or (
                bool(headers.get("x-trellis-token"))
                and hmac.compare_digest(headers["x-trellis-token"], _HTTP_TOKEN)
            )
            if not token_ok:
                await self._reject(
                    send,
                    401,
                    "Unauthorized: missing or invalid token. Open the Trellis UI "
                    "for an authenticated session, or set TRELLIS_API_KEY.",
                )
                return

        origin = headers.get("origin", "")
        if origin and _hostname_of(origin) not in _LOCAL_HOSTNAMES:
            await self._reject(send, 403, "Forbidden: untrusted Origin header")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, message: str):
        body = json.dumps({"error": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


mcp = FastMCP(
    "trellis-core",
    version=VERSION,
    instructions=_SERVER_INSTRUCTIONS,
    strict_input_validation=True,
    mask_error_details=True,
)

# ------------------------------------------------------------------
# State (initialized once at import time)
# ------------------------------------------------------------------
# SpecManager resolves project IDs to repo paths lazily via the lambda, so it
# can be created before _resolve_project_path is defined below.
_spec_manager = SpecManager(
    project_resolver=lambda project_id: _resolve_project_path(project_id)
)

# Cache bridge instances by project path. Bounded: each bridge owns a
# persistent code-graph-mcp subprocess, so an unbounded cache lets an attacker
# (or careless agent) spawn one subprocess per distinct project id.
_BRIDGE_CACHE_MAX = 8
_bridge_cache: "OrderedDict[str, CodeGraphBridge]" = OrderedDict()


def _require_registered(resolved: Path) -> None:
    """Reject absolute paths that are not registered projects.

    Without this, an attacker-controlled project id could point the indexer
    at an arbitrary directory (creating .code-graph inside it and spawning
    the indexer with an attacker-chosen cwd).
    """
    from src.trellis.utils import list_registered_projects

    registered = {
        Path(p).resolve() for p in list_registered_projects().values()
    }
    if resolved not in registered:
        raise ValueError(
            f"'{resolved}' is not a registered project. "
            "Run trellis_sync on it first."
        )


def _resolve_project_path(project_id: str, allow_unregistered: bool = False) -> str:
    """Resolve project ID to actual path.

    Searches multiple locations and prefers paths with a .git directory.
    Never resolves to a directory inside the trellis repo to avoid polluting it.
    Absolute paths are honored only for registered projects unless
    allow_unregistered=True (the trellis_sync path, which registers them).
    """
    if project_id == "trellis":
        return str(Path(__file__).parent)

    path = Path(project_id)
    trellis_root = Path(__file__).parent.resolve()

    if path.is_absolute() and path.exists():
        resolved = path.resolve()
        # Don't allow resolving to inside trellis unless it's trellis itself
        if not _is_inside_trellis(resolved, trellis_root):
            if not allow_unregistered:
                _require_registered(resolved)
            return str(resolved)

    # Collect all candidate paths (excluding trellis internals)
    candidates = []

    # 1. Relative to current directory
    if path.exists():
        resolved = path.resolve()
        if not _is_inside_trellis(resolved, trellis_root):
            candidates.append(resolved)

    # 2. Sibling of trellis root (common pattern: repos/ProjectName)
    sibling = trellis_root.parent / project_id
    if sibling.exists() and sibling.resolve() not in candidates:
        candidates.append(sibling.resolve())

    # 3. Parent of current directory
    parent_sibling = Path.cwd().parent / project_id
    if parent_sibling.exists() and parent_sibling.resolve() not in candidates:
        candidates.append(parent_sibling.resolve())

    # 4. Check if user provided absolute path that doesn't exist yet
    if path.is_absolute():
        if not allow_unregistered:
            _require_registered(path.resolve())
        return str(path)

    if not candidates:
        return project_id  # Fallback

    # Prefer the candidate with a .git directory
    for candidate in candidates:
        if (candidate / ".git").exists():
            return str(candidate)

    # Otherwise return the first candidate
    return str(candidates[0])


def _is_inside_trellis(path: Path, trellis_root: Path) -> bool:
    """Check if a path is inside the trellis repository directory."""
    try:
        path.relative_to(trellis_root)
        return True
    except ValueError:
        return False


def _get_bridge(project_id: str, allow_unregistered: bool = False) -> "CodeGraphBridge":
    """Get or create bridge for project (LRU-bounded; evicted bridges close)."""
    if project_id not in _bridge_cache:
        from src.trellis import CodeGraphBridge

        resolved = _resolve_project_path(project_id, allow_unregistered)
        _bridge_cache[project_id] = CodeGraphBridge(resolved)
        while len(_bridge_cache) > _BRIDGE_CACHE_MAX:
            _old_key, _old_bridge = _bridge_cache.popitem(last=False)
            try:
                _old_bridge.close()
            except Exception:
                pass
    _bridge_cache.move_to_end(project_id)
    return _bridge_cache[project_id]


# ------------------------------------------------------------------
# Skill resource (bootstrap for agents without the trellis-mcp skill)
# ------------------------------------------------------------------

_SKILL_FALLBACK = """\
# Trellis MCP — Getting Started (bundled fallback)

1. Run trellis_sync(project_id="<repo name>", repo_path="<repo root>") and check the project_md field.
2. If project_md is "missing", create <repo root>/project.md before feature-level work:
   one "## Feature: <Name>" section per feature with a plain-text description,
   "### Decisions" (- DEC-001: text (because: rationale)), "### Files" (glob patterns,
   one per line), "### Dependencies" (- Feature: <Name>).
3. Explore: trellis_list_modules, trellis_search_code, trellis_get_function, trellis_feature_info.
4. Before changing code: trellis_analyze_impact. The graph re-indexes edits automatically (file watcher) — just re-analyze; re-run trellis_sync only after large pulls or if results look stale.
5. Document with trellis_create_note ([[wiki links]] and @code mentions).

Install the trellis-mcp skill (skills/ folder in the Trellis distribution) for the full workflow.
"""


def _load_skill_content() -> str:
    """Read the bundled SKILL.md; fall back to a built-in getting-started summary."""
    candidates = [Path(__file__).parent / "skills" / "trellis-mcp" / "SKILL.md"]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).parent / "skills" / "trellis-mcp" / "SKILL.md")
    for path in candidates:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return _SKILL_FALLBACK


@mcp.resource("trellis://skill", mime_type="text/markdown")
async def trellis_skill_resource() -> str:
    """Full trellis-mcp skill document (workflow, project.md format, tool reference).

    Fetch this when the trellis-mcp skill is not installed in your agent —
    it contains everything needed to use these tools effectively.
    """
    return _load_skill_content()


# ------------------------------------------------------------------
# MCP Tools
# ------------------------------------------------------------------


@mcp.tool()
async def trellis_sync(
    project_id: str = "",
    repo_path: str = "",
    config_path: str = ".trellis/config.yaml",
    incremental: bool = False,
) -> str:
    """Sync a project repository into the graph.

    Run ONCE per project to build the initial index. After that the index
    stays fresh by itself: the server watches the project and re-indexes
    edited files automatically. Call again only after large external changes
    (big git pulls, branch switches) or when tool results look stale.
    """
    try:
        # Clear bridge cache for this project to avoid DB lock conflicts
        cache_key = repo_path or project_id
        if cache_key in _bridge_cache:
            _bridge_cache[cache_key].close()
            del _bridge_cache[cache_key]

        # trellis_sync is the registration path: it may point at a repo that
        # has never been synced. All other tools require a registered project.
        from src.trellis import CodeGraphBridge

        resolved = _resolve_project_path(cache_key, allow_unregistered=True)
        bridge = CodeGraphBridge(resolved)

        # Trigger actual indexing
        if incremental:
            sync_result = bridge.incremental_sync()
        else:
            sync_result = bridge.sync_project()

        # After sync, we need a fresh bridge since the old one closed its process
        if cache_key in _bridge_cache:
            _bridge_cache[cache_key].close()
            del _bridge_cache[cache_key]

        # Get fresh bridge for health check
        bridge = _get_bridge(cache_key)
        health = bridge.health_check()

        # Materialize/update the per-feature notes from project.md so the
        # doc graph stays connected. Agents only maintain project.md.
        try:
            from src.trellis.spec_note_sync import sync_feature_notes

            notes_sync = sync_feature_notes(str(bridge.project_path))
        except Exception:
            notes_sync = None

        return _dump(
            {
                "status": "ok",
                "project_id": project_id,
                "nodes": health.get("nodes_count", 0),
                "files": health.get("files_count", 0),
                "message": "Project synced successfully",
                "sync_details": sync_result,
                "feature_notes": notes_sync,
                "project_md": _project_md_status(bridge.project_path),
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_module_overview(
    project_id: str = "",
    module_path: str = "",
) -> str:
    """Get a code module overview (symbols, files, dependencies).

    Use this for inspecting a directory or module in the codebase.
    For feature-level documentation and decisions, use trellis_feature_info.

    Example:
      trellis_module_overview(project_id='tui.image-editor', module_path='apps/image-editor/src/js/component')
    """
    MAX_FILES = 50
    MAX_SYMBOLS_PER_FILE = 50

    try:
        bridge = _get_bridge(project_id)

        import sqlite3
        from src.trellis.utils import resolve_code_graph_db

        db_path = resolve_code_graph_db(bridge.project_path)
        if not db_path.exists():
            return _dump(
                {"error": "No code-graph database found. Run trellis_sync first."}
            )

        like_pattern = f"%{module_path}%"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Files in this module and their symbols
        cursor.execute(
            """
            SELECT f.path, n.type, n.name, n.qualified_name, n.start_line
            FROM nodes n
            JOIN files f ON n.file_id = f.id
            WHERE f.path LIKE ?
              AND n.type IN ('function', 'method', 'class')
            ORDER BY f.path, n.start_line
        """,
            (like_pattern,),
        )

        files: Dict[str, List[Dict[str, Any]]] = {}
        truncated = False
        for row in cursor.fetchall():
            file_path, kind, name, qname, line = row
            if len(files) >= MAX_FILES and file_path not in files:
                truncated = True
                continue
            symbols = files.setdefault(file_path, [])
            if len(symbols) >= MAX_SYMBOLS_PER_FILE:
                truncated = True
                continue
            symbols.append(
                {
                    "kind": kind,
                    "name": name,
                    "qualified_name": qname or name,
                    "line": line,
                }
            )

        # Edges from this module to other modules
        cursor.execute(
            """
            SELECT DISTINCT tf.path, e.relation, COUNT(*) as cnt
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            JOIN nodes t ON e.target_id = t.id
            JOIN files sf ON s.file_id = sf.id
            JOIN files tf ON t.file_id = tf.id
            WHERE sf.path LIKE ?
              AND tf.path NOT LIKE ?
              AND e.relation IN ('calls', 'imports')
            GROUP BY tf.path, e.relation
            ORDER BY cnt DESC
            LIMIT 30
        """,
            (like_pattern, like_pattern),
        )
        outgoing = [
            {"file_path": r[0], "relation": r[1], "count": r[2]}
            for r in cursor.fetchall()
        ]

        conn.close()

        return _dump(
            {
                "module_path": module_path,
                "files": files,
                "outgoing_edges": outgoing,
                "truncated": truncated,
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_analyze_impact(
    project_id: str = "",
    function_path: str = "",
    depth_mode: str = "standard",
) -> str:
    """Analyze impact of changing a function.

    Returns both technical and feature-level impact.
    """
    try:
        bridge = _get_bridge(project_id)

        # Get feature impact report
        report = bridge.get_feature_impact(function_path, depth=3)

        # Format for MCP
        result = {
            "symbol": function_path,
            "risk_level": report.get("risk_level", "unknown"),
            "affected_functions": report.get("affected_functions_count", 0),
            "affected_files": report.get("affected_files_count", 0),
            "feature_impacts": [
                {
                    "feature": fi["feature_name"],
                    "functions": len(fi["impacted_functions"]),
                    "decisions": [d["id"] for d in fi["affected_decisions"]],
                    "risks": fi["risk_flags"],
                }
                for fi in report.get("feature_impacts", [])
            ],
            "development_pointers": report.get("development_pointers", [])[
                :10
            ],  # Limit
            "divergence_warnings": report.get("divergence_warnings", []),
        }

        return _dump(result)
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_get_function(
    project_id: str = "",
    function_path: str = "",
) -> str:
    """Get details for a specific function.

    function_path can be:
    - Just the function name: "create_llm"
    - Qualified name: "AIFactory.create_llm"
    - File:function format: "backend/ai/factory.py:create_llm"
    """
    try:
        bridge = _get_bridge(project_id)

        # Handle file:function format by extracting just the function name
        symbol = function_path
        if ":" in function_path:
            symbol = function_path.split(":")[-1]

        # Try direct lookup first
        node = bridge.get_ast_node(symbol)

        # If not found, try searching by name in SQLite
        if not node or (
            isinstance(node, dict) and ("error" in node or not node.get("name"))
        ):
            import sqlite3
            from src.trellis.utils import resolve_code_graph_db

            db_path = resolve_code_graph_db(bridge.project_path)
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                # Search by name or qualified_name
                cursor.execute(
                    """
                    SELECT n.name, n.qualified_name, f.path, n.start_line, n.end_line, n.type, n.signature, n.code_content
                    FROM nodes n
                    JOIN files f ON n.file_id = f.id
                    WHERE (n.name = ? OR n.qualified_name = ?)
                      AND n.type IN ('function', 'method')
                    LIMIT 5
                """,
                    (symbol, symbol),
                )

                rows = cursor.fetchall()
                conn.close()

                if rows:
                    # Return the first match
                    row = rows[0]
                    node = {
                        "name": row[0],
                        "qualified_name": row[1] or row[0],
                        "file_path": row[2],
                        "start_line": row[3],
                        "end_line": row[4],
                        "type": row[5],
                        "signature": row[6] or "",
                        "code_content": row[7] or "",
                    }
                else:
                    node = {"error": f"Function '{symbol}' not found in index"}

        # Cap source payload so large functions don't flood the context
        if isinstance(node, dict) and isinstance(node.get("code_content"), str):
            node["code_content"] = _truncate(
                node["code_content"], MAX_CODE_CONTENT_CHARS
            )

        return _dump(node)
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_search_code(
    project_id: str = "",
    query: str = "",
    limit: int = 10,
) -> str:
    """Search for code symbols (functions, methods, classes) by keyword.

    For searching knowledge notes, use trellis_search_notes.

    Example:
      trellis_search_code(project_id='tui.image-editor', query='addIcon', limit=5)
    """
    try:
        bridge = _get_bridge(project_id)
        results = bridge.search(query, limit=limit)
        return _dump({"results": results})
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_ast_search(
    project_id: str = "",
    query: str = "",
    node_type: str = "",
    returns: str = "",
    params: str = "",
    limit: int = 20,
) -> str:
    """Structural AST search with type/signature filters.

    Finds symbols by structure, not just keyword: filter by node type
    (function/method/class/...), return type, or parameter signature. At least
    one of query/node_type/returns/params is required. Complements
    trellis_search_code (keyword/semantic search).

    Examples:
      trellis_ast_search(project_id='tui.image-editor', node_type='class')
      trellis_ast_search(project_id='tui.image-editor', query='render', node_type='method')
      trellis_ast_search(project_id='tui.image-editor', returns='Promise')
    """
    try:
        bridge = _get_bridge(project_id)
        results = bridge.ast_search(
            query=query or None,
            node_type=node_type or None,
            returns=returns or None,
            params=params or None,
            limit=limit,
        )
        return _dump({"results": results})
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_list_modules(
    project_id: str = "",
) -> str:
    """List all code modules/directories in the project with symbol counts.

    For project.md feature specifications, use trellis_feature_info.
    """
    try:
        bridge = _get_bridge(project_id)
        pmap = bridge.project_map()
        modules = pmap.get("modules", [])
        return _dump(
            {
                "modules": [
                    {
                        "path": m.get("path", "unknown"),
                        "files": m.get("files", 0),
                        "symbols": m.get("symbols", 0),
                        "language": m.get("language", ""),
                    }
                    for m in modules
                ]
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_feature_info(
    project_id: str = "",
    feature_name: str = "",
) -> str:
    """Get comprehensive information about a feature.

    Returns project.md spec, related knowledge notes, all functions in the
    feature, and the most central (hot) functions for blast-radius analysis.

    Example: trellis_feature_info(project_id='tui.image-editor', feature_name='Icons')
    """
    try:
        bridge = _get_bridge(project_id)
        info = bridge.get_feature_info(feature_name)

        # Cap note payload so long architecture notes don't flood the context
        note = info.get("note")
        if isinstance(note, dict) and isinstance(note.get("content"), str):
            note["content"] = _truncate(note["content"], MAX_NOTE_CONTENT_CHARS)

        if not info.get("found_in_spec"):
            info["project_md"] = _project_md_status(bridge.project_path)

        return _dump(info)
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_trace_path(
    project_id: str = "",
    from_feature: str = "",
    to_feature: str = "",
) -> str:
    """Trace dependency paths between two features or code modules.

    Accepts either project.md feature names (e.g. 'Icons') or code module paths
    (e.g. 'apps/image-editor/src/js/ui'). Returns direct and one-hop indirect
    call/import edges between them.

    Example:
      trellis_trace_path(project_id='tui.image-editor', from_feature='Icons', to_feature='Graphics')
    """
    try:
        bridge = _get_bridge(project_id)

        import sqlite3
        from src.trellis.utils import resolve_code_graph_db

        db_path = resolve_code_graph_db(bridge.project_path)
        if not db_path.exists():
            return _dump(
                {"error": "No code-graph database found. Run trellis_sync first."}
            )

        # Resolve feature names to file patterns via project.md
        from src.trellis.feature_impact import ProjectContextParser

        context = ProjectContextParser(str(bridge.project_path))
        from_patterns = [f"%{from_feature}%"]
        to_patterns = [f"%{to_feature}%"]

        if from_feature in context.features:
            from_patterns = [
                p.replace("**", "%").replace("*", "%")
                for p in context.features[from_feature].file_patterns
            ]
        if to_feature in context.features:
            to_patterns = [
                p.replace("**", "%").replace("*", "%")
                for p in context.features[to_feature].file_patterns
            ]

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        def _files_for(patterns):
            files = set()
            for pat in patterns:
                cursor.execute(
                    "SELECT DISTINCT path FROM files WHERE path LIKE ?", (pat,)
                )
                files.update(row[0] for row in cursor.fetchall())
            return files

        from_files = _files_for(from_patterns)
        to_files = _files_for(to_patterns)

        if not from_files:
            return _dump({"error": f"Feature/module '{from_feature}' not found"})
        if not to_files:
            return _dump({"error": f"Feature/module '{to_feature}' not found"})

        # Direct edges: source in from_files, target in to_files
        from_ph = ",".join("?" * len(from_files))
        to_ph = ",".join("?" * len(to_files))
        cursor.execute(
            f"""
            SELECT DISTINCT sf.path, tf.path, s.name, e.relation
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            JOIN nodes t ON e.target_id = t.id
            JOIN files sf ON s.file_id = sf.id
            JOIN files tf ON t.file_id = tf.id
            WHERE e.relation IN ('calls', 'imports')
              AND sf.path IN ({from_ph})
              AND tf.path IN ({to_ph})
            LIMIT 20
        """,
            tuple(from_files) + tuple(to_files),
        )
        direct = [
            {"from_file": r[0], "to_file": r[1], "symbol": r[2], "relation": r[3]}
            for r in cursor.fetchall()
        ]

        # One-hop indirect: from_files -> intermediate (not in from/to)
        all_files = from_files | to_files
        all_ph = ",".join("?" * len(all_files))
        cursor.execute(
            f"""
            SELECT DISTINCT sf.path, tf.path, s.name, e.relation
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            JOIN nodes t ON e.target_id = t.id
            JOIN files sf ON s.file_id = sf.id
            JOIN files tf ON t.file_id = tf.id
            WHERE e.relation IN ('calls', 'imports')
              AND sf.path IN ({from_ph})
              AND tf.path NOT IN ({all_ph})
            LIMIT 50
        """,
            tuple(from_files) + tuple(all_files),
        )
        outbound = [
            {"from_file": r[0], "to_file": r[1], "symbol": r[2], "relation": r[3]}
            for r in cursor.fetchall()
        ]

        cursor.execute(
            f"""
            SELECT DISTINCT sf.path, tf.path, s.name, e.relation
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            JOIN nodes t ON e.target_id = t.id
            JOIN files sf ON s.file_id = sf.id
            JOIN files tf ON t.file_id = tf.id
            WHERE e.relation IN ('calls', 'imports')
              AND sf.path NOT IN ({all_ph})
              AND tf.path IN ({to_ph})
            LIMIT 50
        """,
            tuple(all_files) + tuple(to_files),
        )
        inbound = [
            {"from_file": r[0], "to_file": r[1], "symbol": r[2], "relation": r[3]}
            for r in cursor.fetchall()
        ]

        conn.close()

        return _dump(
            {
                "from_feature": from_feature,
                "to_feature": to_feature,
                "from_files_count": len(from_files),
                "to_files_count": len(to_files),
                "from_files_sample": sorted(from_files)[:5],
                "to_files_sample": sorted(to_files)[:5],
                "direct_connections": direct,
                "direct_connection_count": len(direct),
                "outbound_intermediate": outbound[:10],
                "inbound_intermediate": inbound[:10],
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_get_graph(
    project_id: str = "",
    max_nodes: int = 200,
) -> str:
    """Get code graph data (nodes, edges, modules) for the project.

    Large graphs are expensive for agents: when the project has more than
    max_nodes functions, a simplified view (modules + one representative
    function per file) is returned instead of the full dump. Prefer
    trellis_analyze_impact / trellis_trace_path for targeted questions.

    Args:
        project_id: Project to analyze
        max_nodes: Max function nodes before switching to the simplified view
    """
    try:
        bridge = _get_bridge(project_id)
        graph = bridge.get_graph_for_visualizer(max_nodes=max_nodes)
        return _dump(graph)
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_tour(
    project_id: str = "",
    path: str = "",
) -> str:
    """Get a dependency-ordered reading tour of the project (or a subtree).

    Lists modules from foundational to entry-point (Kahn topological sort over
    import edges), so reading top-to-bottom orients you from the ground up.
    Each entry includes the module path, its role, what it depends on, how many
    modules depend on it, key symbols, and whether it sits in an import cycle.

    Use when onboarding to a codebase or exploring an unfamiliar module.

    Example:
      trellis_tour(project_id='tui.image-editor')
      trellis_tour(project_id='tui.image-editor', path='apps/image-editor/src/js')
    """
    try:
        bridge = _get_bridge(project_id)
        result = bridge.tour(path or None)
        return _dump(result)
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_trace_http_route(
    project_id: str = "",
    route_path: str = "",
    depth: int = 3,
) -> str:
    """Trace an HTTP route to its handler and downstream calls (web projects).

    Given a route like 'GET /api/users' or '/api/users', returns the handler
    symbol and its downstream call chain (middleware included by default,
    capped at depth). Only useful for projects with indexed HTTP routes
    (FastAPI/Flask/Express/etc.); others return a 'no routes found' result.

    Example:
      trellis_trace_http_route(project_id='my-api', route_path='POST /api/login')
    """
    try:
        bridge = _get_bridge(project_id)
        result = bridge.trace_http_route(route_path, depth=depth)
        return _dump(result)
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_project_health(
    project_id: str = "",
    limit: int = 15,
) -> str:
    """Architecture-health snapshot: chokepoints, import cycles, surprising couplings.

    Returns three sections:
      chokepoints — functions ranked by betweenness centrality over the call
        graph (structural bridges; few callers but high cross-cluster traffic)
      import_cycles — circular file-level import dependencies (empty = healthy)
      surprising_connections — uncertain or sole-bridge cross-module calls/
        references edges worth auditing before a refactor

    Example:
      trellis_project_health(project_id='tui.image-editor', limit=10)
    """
    try:
        bridge = _get_bridge(project_id)
        return _dump(bridge.project_health(limit))
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_detect_hotspots(
    project_id: str = "",
    limit: int = 20,
) -> str:
    """Find high-centrality functions (potential hotspots).

    Returns functions with the most incoming calls/imports.
    """
    try:
        bridge = _get_bridge(project_id)

        import sqlite3
        from src.trellis.utils import resolve_code_graph_db

        db_path = resolve_code_graph_db(bridge.project_path)
        if not db_path.exists():
            return _dump(
                {"error": "No code-graph database found. Run trellis_sync first."}
            )

        hotspots = []
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT t.name, t.qualified_name, f.path, t.start_line, COUNT(*) as in_degree
                FROM edges e
                JOIN nodes t ON e.target_id = t.id
                JOIN files f ON t.file_id = f.id
                WHERE e.relation IN ('calls', 'imports', 'uses')
                  AND t.type IN ('function', 'method')
                GROUP BY t.id
                ORDER BY in_degree DESC
                LIMIT ?
            """,
                (limit,),
            )
            hotspots = [
                {
                    "name": row[0],
                    "qualified_name": row[1] or row[0],
                    "file_path": row[2],
                    "line": row[3],
                    "in_degree": row[4],
                }
                for row in cursor.fetchall()
            ]
            conn.close()
        except Exception:
            pass

        return _dump({"hotspots": hotspots})
    except Exception as e:
        return _dump({"error": str(e)})


async def trellis_analyze_diff(
    project_id: str = "",
    diff: str = "",
    compare_branch: str = "",
) -> str:
    """Analyze impact of a code diff. (Disabled by default)

    Automatically detects changed functions from git diff and runs impact analysis.
    Broad diffs are expensive (one impact analysis per changed function), so this
    tool is only registered when TRELLIS_ENABLE_DIFF_ANALYSIS=1 and is hard-capped
    via TRELLIS_MAX_DIFF_CHARS / TRELLIS_MAX_DIFF_FILES / TRELLIS_MAX_DIFF_FUNCTIONS.
    For per-function checks prefer trellis_analyze_impact.

    Args:
        project_id: Project to analyze
        diff: Optional raw diff string. If not provided, fetches from git.
        compare_branch: Branch to compare against (default: origin/main or main)
    """
    try:
        import re
        import sqlite3
        from src.trellis.utils import resolve_code_graph_db

        resolved = _resolve_project_path(project_id)

        # Resolve symlinks/junctions to real path for git operations
        resolved_real = str(Path(resolved).resolve())

        # Step 1: Get diff if not provided
        if not diff:
            try:
                import git

                repo = git.Repo(resolved_real)

                # Determine what to diff against
                if compare_branch:
                    target = compare_branch
                else:
                    # Try common default branches
                    for branch in ["origin/main", "origin/master", "main", "master"]:
                        try:
                            repo.rev_parse(branch)
                            target = branch
                            break
                        except git.BadName:
                            continue
                    else:
                        # No remote branch, get unstaged changes
                        diff = repo.git.diff()
                        target = None

                if target and not diff:
                    diff = repo.git.diff(target)

                if not diff:
                    return _dump(
                        {
                            "status": "no_changes",
                            "message": "No changes detected in git working tree",
                            "project": project_id,
                        }
                    )

            except ImportError:
                return _dump(
                    {
                        "error": "GitPython not installed. Either install it (pip install GitPython) or provide the diff parameter directly: trellis_analyze_diff(project_id='...', diff='...')",
                    }
                )
            except git.InvalidGitRepositoryError:
                return _dump(
                    {
                        "error": f"'{resolved_real}' is not a git repository.\n\nTo fix:\n1. Pass the absolute path: trellis_analyze_diff(project_id='/path/to/repo')\n2. Or provide the diff directly: trellis_analyze_diff(project_id='{project_id}', diff='...')",
                    }
                )

        # Guard against huge diffs that cannot be processed within tool timeouts
        if len(diff) > MAX_DIFF_CHARS:
            return _dump(
                {
                    "error": "diff_too_large",
                    "size_chars": len(diff),
                    "limit_chars": MAX_DIFF_CHARS,
                    "hint": (
                        "Diff is too large to analyze in one call. Narrow the scope "
                        "(e.g. compare a single commit or file) or run "
                        "trellis_analyze_impact on the specific functions you changed. "
                        "Raise TRELLIS_MAX_DIFF_CHARS to override."
                    ),
                }
            )

        # Step 2: Parse unified diff to find changed files and line numbers
        changed_files = {}  # file_path -> set of changed line numbers
        current_file = None
        current_line = 0

        for line in diff.split("\n"):
            # File header: --- a/path or +++ b/path
            if line.startswith("--- ") or line.startswith("+++ "):
                # Extract file path, skip /dev/null
                path = line[4:].split("\t")[0]
                if path.startswith("a/"):
                    path = path[2:]
                elif path.startswith("b/"):
                    path = path[2:]
                if path != "/dev/null":
                    current_file = path
                    if current_file not in changed_files:
                        changed_files[current_file] = set()

            # Hunk header: @@ -old_start,old_len +new_start,new_len @@
            elif line.startswith("@@") and current_file:
                match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if match:
                    current_line = int(match.group(1))

            # Added line (in new file)
            elif line.startswith("+") and not line.startswith("+++") and current_file:
                changed_files[current_file].add(current_line)
                current_line += 1

            # Removed line (skip in new file line counting)
            elif line.startswith("-") and not line.startswith("---") and current_file:
                # Line was removed, note it but don't increment counter
                pass

            # Context line
            elif current_file and not line.startswith("\\"):
                current_line += 1

        # Filter out files with no actual line changes
        changed_files = {f: lines for f, lines in changed_files.items() if lines}

        if not changed_files:
            return _dump(
                {
                    "status": "no_changes",
                    "message": "No file changes detected in diff",
                    "diff_length": len(diff),
                }
            )

        # Guard against broad diffs: per-function impact analysis spawns a
        # subprocess per function and cannot finish within tool timeouts.
        if len(changed_files) > MAX_DIFF_FILES:
            return _dump(
                {
                    "status": "diff_too_broad",
                    "changed_files_count": len(changed_files),
                    "limit_files": MAX_DIFF_FILES,
                    "changed_files": sorted(changed_files.keys())[:MAX_DIFF_FILES],
                    "hint": (
                        "Too many changed files for whole-diff impact analysis. "
                        "Run trellis_analyze_impact on the specific functions you "
                        "changed, or narrow the diff (single commit/file). Raise "
                        "TRELLIS_MAX_DIFF_FILES to override."
                    ),
                }
            )

        # Step 3: Query SQLite to find affected functions
        db_path = resolve_code_graph_db(resolved)
        affected_functions = []

        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                for file_path, line_numbers in changed_files.items():
                    if not line_numbers:
                        continue

                    # Find functions in this file that overlap with changed lines
                    cursor.execute(
                        """
                        SELECT DISTINCT n.name, n.qualified_name, f.path, 
                               n.start_line, n.end_line, n.type
                        FROM nodes n
                        JOIN files f ON n.file_id = f.id
                        WHERE f.path LIKE ?
                          AND n.type IN ('function', 'method', 'class')
                          AND n.start_line <= ?
                          AND n.end_line >= ?
                    """,
                        (f"%{file_path}%", max(line_numbers), min(line_numbers)),
                    )

                    for row in cursor.fetchall():
                        # Verify actual overlap with changed lines
                        func_start, func_end = row[3], row[4]
                        changed_in_func = [
                            line
                            for line in line_numbers
                            if func_start <= line <= func_end
                        ]

                        if changed_in_func:
                            affected_functions.append(
                                {
                                    "name": row[0],
                                    "qualified_name": row[1] or row[0],
                                    "file_path": row[2],
                                    "type": row[5],
                                    "changed_lines": len(changed_in_func),
                                    "line_range": [func_start, func_end],
                                }
                            )

                conn.close()
            except Exception:
                # Continue without function-level details
                pass

        # Step 4: Run impact analysis on unique affected functions (hard-capped:
        # each analysis spawns subprocesses, so unbounded loops time out)
        bridge = _get_bridge(project_id)
        impact_results = []
        analyzed_symbols = set()
        functions_capped = False

        for func in affected_functions:
            symbol = func["qualified_name"] or func["name"]
            if symbol in analyzed_symbols:
                continue
            if len(analyzed_symbols) >= MAX_DIFF_FUNCTIONS:
                functions_capped = True
                break
            analyzed_symbols.add(symbol)

            try:
                report = bridge.get_feature_impact(symbol, depth=2)
                impact_results.append(
                    {
                        "symbol": symbol,
                        "file_path": func["file_path"],
                        "type": func["type"],
                        "changed_lines": func["changed_lines"],
                        "risk_level": report.get("risk_level", "unknown"),
                        "affected_functions": report.get("affected_functions_count", 0),
                        "affected_files": report.get("affected_files_count", 0),
                        "feature_impacts": [
                            {
                                "feature": fi["feature_name"],
                                "functions": len(fi["impacted_functions"]),
                            }
                            for fi in report.get("feature_impacts", [])
                        ],
                    }
                )
            except Exception as e:
                impact_results.append(
                    {
                        "symbol": symbol,
                        "file_path": func["file_path"],
                        "error": str(e),
                    }
                )

        # Step 5: Calculate overall risk
        risk_scores = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}
        max_risk = max(
            [
                risk_scores.get(r["risk_level"], 0)
                for r in impact_results
                if "risk_level" in r
            ],
            default=0,
        )
        risk_labels = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: "unknown"}
        overall_risk = risk_labels.get(max_risk, "unknown")

        total_affected_funcs = sum(
            r.get("affected_functions", 0)
            for r in impact_results
            if "affected_functions" in r
        )

        return _dump(
            {
                "status": "ok",
                "project": project_id,
                "overall_risk": overall_risk,
                "changed_files_count": len(changed_files),
                "changed_files": list(changed_files.keys()),
                "affected_functions_count": len(affected_functions),
                "unique_functions_analyzed": len(impact_results),
                "functions_capped": functions_capped,
                "max_functions": MAX_DIFF_FUNCTIONS if functions_capped else None,
                "total_downstream_affected": total_affected_funcs,
                "functions": impact_results,
            }
        )

    except Exception as e:
        return _dump({"error": str(e)})


# Diff analysis is opt-in: broad diffs time out and flood the agent context.
if DIFF_ANALYSIS_ENABLED:
    mcp.tool()(trellis_analyze_diff)


@mcp.tool()
async def trellis_get_boundary_map(
    project_id: str = "",
) -> str:
    """Get module boundary map (modules and cross-module dependencies)."""
    try:
        bridge = _get_bridge(project_id)
        pmap = bridge.project_map()
        modules = pmap.get("modules", [])
        deps = pmap.get("dependencies", [])
        return _dump(
            {
                "modules": [m.get("path") for m in modules],
                "dependencies": deps,
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


# ------------------------------------------------------------------
# Knowledge Graph Tools
# ------------------------------------------------------------------


@mcp.tool()
async def trellis_create_note(
    project_id: str = "",
    note_id: str = "",
    title: str = "",
    content: str = "",
    tags: str = "",
) -> str:
    """Create or update a knowledge note.

    Notes support markdown with [[links]] and @mentions.
    """
    try:
        from src.trellis.knowledge_graph import NoteGraph

        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)

        tag_list = [t.strip() for t in tags.split(",")] if tags else []
        note = graph.save_note(note_id, content, title=title, tags=tag_list)

        return _dump(
            {
                "status": "ok",
                "note_id": note.id,
                "title": note.title,
                "links": note.links,
                "mentions": note.mentions,
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_get_note(
    project_id: str = "",
    note_id: str = "",
) -> str:
    """Get a knowledge note by ID."""
    try:
        from src.trellis.knowledge_graph import NoteGraph

        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        note = graph.get_note(note_id)

        if not note:
            return _dump({"error": f"Note '{note_id}' not found"})

        return _dump(
            {
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "tags": note.tags,
                "links": note.links,
                "mentions": note.mentions,
                "backlinks": graph.get_backlinks(note_id),
                "created": note.created_at,
                "updated": note.updated_at,
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_search_notes(
    project_id: str = "",
    query: str = "",
) -> str:
    """Search knowledge notes by content."""
    try:
        from src.trellis.knowledge_graph import NoteGraph

        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        results = graph.search_notes(query)

        return _dump(
            {
                "query": query,
                "results": [
                    {
                        "id": n.id,
                        "title": n.title,
                        "excerpt": n.content[:200] + "..."
                        if len(n.content) > 200
                        else n.content,
                        "tags": n.tags,
                    }
                    for n in results
                ],
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_delete_note(
    project_id: str = "",
    note_id: str = "",
) -> str:
    """Delete a knowledge note."""
    try:
        from src.trellis.knowledge_graph import NoteGraph

        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        success = graph.delete_note(note_id)

        return _dump(
            {
                "status": "ok" if success else "error",
                "message": f"Note '{note_id}' deleted"
                if success
                else f"Note '{note_id}' not found",
            }
        )
    except Exception as e:
        return _dump({"error": str(e)})


@mcp.tool()
async def trellis_knowledge_graph(
    project_id: str = "",
    include_code: bool = False,
) -> str:
    """Get the knowledge graph (notes + edges).

    Code nodes are excluded by default: including them dumps every function in
    the project into the agent's context. Pass include_code=True only when you
    specifically need note-to-code edges. Note contents are truncated; use
    trellis_get_note for full content.

    Args:
        project_id: Project to analyze
        include_code: Include code function nodes (large; default False)
    """
    try:
        from src.trellis.knowledge_graph import NoteGraph

        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        data = graph.build_graph(include_code=include_code)

        # Truncate note contents in graph nodes; full text via trellis_get_note
        for node in data.get("nodes", []):
            if isinstance(node.get("content"), str):
                node["content"] = _truncate(node["content"], MAX_NOTE_CONTENT_CHARS)

        return _dump(data)
    except Exception as e:
        return _dump({"error": str(e)})


# ------------------------------------------------------------------
# HTTP Routes
# ------------------------------------------------------------------


@mcp.custom_route("/", methods=["GET"])
async def root(request: Request):
    """Serve visualizer HTML with the launch token embedded."""
    visualizer_path = Path(__file__).parent / "visualizer.html"
    if visualizer_path.exists():
        html = visualizer_path.read_text(encoding="utf-8")
        token_script = f'<script>window.TRELLIS_TOKEN = "{_HTTP_TOKEN}";</script>'
        if "</head>" in html:
            html = html.replace("</head>", token_script + "</head>", 1)
        else:
            html = token_script + html
        return HTMLResponse(html)
    return JSONResponse({"message": "Trellis MCP Server", "version": VERSION})


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    """Health check."""
    return JSONResponse({"status": "ok", "version": VERSION})


@mcp.custom_route("/vendor/{filename}", methods=["GET"])
async def vendor_assets(request: Request):
    """Serve vendored JS libraries (d3, marked) — local, no CDN.

    Loaded via <script src> from the UI page, which cannot send custom
    headers, so these public static assets are exempt from the token check
    (the guard middleware whitelists the /vendor/ prefix too).
    """
    filename = request.path_params["filename"]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    asset = Path(__file__).parent / "vendor" / filename
    if not asset.is_file():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(asset)


@mcp.custom_route("/graph/{project_id}", methods=["GET"])
async def graph_get(request: Request):
    """Get graph data."""
    project_id = request.path_params["project_id"]
    try:
        bridge = _get_bridge(project_id)
        graph = bridge.get_graph_for_visualizer()
        return JSONResponse(graph)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/graph/{project_id}/sync", methods=["POST"])
async def graph_sync(request: Request):
    """Full index rebuild. Heavy — edits are otherwise indexed automatically
    by the server-side file watcher (armed by the bridge on every spawn), so
    this is only needed after large external changes or when stale."""
    import asyncio

    project_id = request.path_params["project_id"]
    try:
        bridge = _get_bridge(project_id)
        result = await asyncio.to_thread(bridge.sync_project)
        return JSONResponse(
            {
                "success": result.get("success", False),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
            }
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/graph/{project_id}/status", methods=["GET"])
async def graph_status(request: Request):
    """Index status including whether the file watcher is active."""
    project_id = request.path_params["project_id"]
    try:
        bridge = _get_bridge(project_id)
        return JSONResponse(bridge.health_check())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/graph/{project_id}/tour", methods=["GET"])
async def graph_tour(request: Request):
    """Dependency-ordered reading tour (foundational -> entry-point modules)."""
    import asyncio

    project_id = request.path_params["project_id"]
    path = request.query_params.get("path") or None
    try:
        bridge = _get_bridge(project_id)
        result = await asyncio.to_thread(bridge.tour, path)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/graph/{project_id}/health", methods=["GET"])
async def graph_health(request: Request):
    """Architecture-health snapshot (chokepoints, import cycles, surprising edges)."""
    import asyncio

    project_id = request.path_params["project_id"]
    limit = request.query_params.get("limit")
    try:
        bridge = _get_bridge(project_id)
        result = await asyncio.to_thread(
            bridge.project_health, int(limit) if limit else 15
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/graph/{project_id}/impact/{symbol}", methods=["GET"])
async def graph_impact(request: Request):
    """Get impact graph."""
    project_id = request.path_params["project_id"]
    symbol = request.path_params["symbol"]
    try:
        bridge = _get_bridge(project_id)
        graph = bridge.get_impact_graph(symbol)
        return JSONResponse(graph)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/feature/{project_id}/impact/{symbol}", methods=["GET"])
async def feature_impact(request: Request):
    """Get feature impact report."""
    project_id = request.path_params["project_id"]
    symbol = request.path_params["symbol"]
    try:
        bridge = _get_bridge(project_id)
        report = bridge.get_feature_impact(symbol)
        return JSONResponse(report)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/feature/{project_id}/pointers/{symbol}", methods=["GET"])
async def feature_pointers(request: Request):
    """Get development pointers."""
    project_id = request.path_params["project_id"]
    symbol = request.path_params["symbol"]
    try:
        bridge = _get_bridge(project_id)
        pointers = bridge.get_development_pointers(symbol)
        return JSONResponse({"pointers": pointers})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/spec/{project_id}", methods=["GET", "POST"])
async def spec_handler(request: Request):
    """Handle project spec."""
    project_id = request.path_params["project_id"]

    if request.method == "GET":
        spec = _spec_manager.load_spec(project_id)
        repo_spec_path = str(Path(_resolve_project_path(project_id)) / "project.md")
        if spec is None:
            template = _spec_manager.create_template(project_id)
            return JSONResponse(
                {
                    "project_id": project_id,
                    "status": "no_spec",
                    "content": template,
                    "path": repo_spec_path,
                }
            )
        return JSONResponse(
            {
                "project_id": project_id,
                "status": "ok",
                "content": spec.content,
                "path": spec.source_path,
            }
        )

    else:  # POST
        body = await request.json()
        content = body.get("content", "")
        path = _spec_manager.save_spec(project_id, content)

        # Refresh the auto-generated feature notes so the doc graph follows
        # the spec the agent just wrote.
        try:
            from src.trellis.spec_note_sync import sync_feature_notes

            notes_sync = sync_feature_notes(_resolve_project_path(project_id))
        except Exception:
            notes_sync = None

        return JSONResponse(
            {
                "project_id": project_id,
                "status": "ok",
                "path": str(path),
                "feature_notes": notes_sync,
            }
        )


@mcp.custom_route("/spec/{project_id}/alignment", methods=["GET"])
async def spec_alignment(request: Request):
    """Verify how well project.md features align with the synced code graph.

    For each parsed feature: which indexed files its glob patterns match, how
    many functions map to it, and what is wrong or missing (no patterns,
    patterns matching nothing, zero functions, missing description).
    Also reports indexed files not covered by any feature.
    """
    project_id = request.path_params["project_id"]
    try:
        from src.trellis.feature_impact import ProjectContextParser
        from src.trellis.utils import resolve_code_graph_db

        bridge = _get_bridge(project_id)
        parser = ProjectContextParser(str(bridge.project_path))
        features = parser.get_all_features()

        # All indexed source files from the code graph DB
        files = []
        db_path = resolve_code_graph_db(str(bridge.project_path))
        if db_path.exists():
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            files = [row[0] for row in conn.execute("SELECT path FROM files")]
            conn.close()

        def match_files(patterns):
            matched = []
            for path in files:
                for pattern in patterns:
                    regex = pattern.replace("**", ".*").replace("*", "[^/]*")
                    if re.search(regex, path):
                        matched.append(path)
                        break
            return matched

        feature_rows = []
        covered_files = set()
        for name, feature in features.items():
            matched = match_files(feature.file_patterns)
            covered_files.update(matched)
            functions = bridge.get_feature_functions(name)
            issues = []
            if not feature.file_patterns:
                issues.append("no file patterns - add a '### Files' section")
            elif not matched:
                issues.append("file patterns match no indexed files")
            if not functions:
                issues.append("no functions mapped to this feature")
            if not feature.description:
                issues.append("no description")
            feature_rows.append(
                {
                    "name": name,
                    "description": feature.description,
                    "file_patterns": feature.file_patterns,
                    "matched_files": matched,
                    "matched_file_count": len(matched),
                    "function_count": len(functions),
                    "decision_count": len(feature.decisions),
                    "constraints_count": len(feature.constraints),
                    "dependencies": feature.dependencies,
                    "issues": issues,
                }
            )

        unmapped_files = [f for f in files if f not in covered_files]
        return JSONResponse(
            {
                "project_id": project_id,
                "spec_status": _project_md_status(bridge.project_path),
                "total_files": len(files),
                "covered_file_count": len(covered_files),
                "unmapped_files": unmapped_files,
                "features": feature_rows,
                "summary": {
                    "feature_count": len(feature_rows),
                    "features_with_issues": sum(
                        1 for f in feature_rows if f["issues"]
                    ),
                    "fully_aligned": all(not f["issues"] for f in feature_rows)
                    and not unmapped_files,
                },
            }
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/knowledge-graph/{project_id}", methods=["GET"])
async def knowledge_graph_get(request: Request):
    """Get knowledge graph data (notes + code)."""
    project_id = request.path_params["project_id"]
    try:
        from src.trellis.knowledge_graph import NoteGraph

        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        data = graph.build_graph(include_code=True)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/note/{project_id}/{note_id}", methods=["GET", "POST", "DELETE"])
async def note_handler(request: Request):
    """Get, save, or delete a knowledge note."""
    project_id = request.path_params["project_id"]
    note_id = request.path_params["note_id"]

    try:
        from src.trellis.knowledge_graph import NoteGraph

        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)

        if request.method == "GET":
            note = graph.get_note(note_id)
            if not note:
                return JSONResponse(
                    {"error": f"Note '{note_id}' not found"}, status_code=404
                )
            return JSONResponse(
                {
                    "id": note.id,
                    "title": note.title,
                    "content": note.content,
                    "tags": note.tags,
                    "links": note.links,
                    "mentions": note.mentions,
                    "backlinks": graph.get_backlinks(note_id),
                }
            )
        elif request.method == "POST":
            body = await request.json()
            content = body.get("content", "")
            title = body.get("title", note_id)
            tags = body.get("tags", [])
            note = graph.save_note(note_id, content, title=title, tags=tags)
            return JSONResponse(
                {"status": "ok", "note_id": note.id, "title": note.title}
            )
        elif request.method == "DELETE":
            success = graph.delete_note(note_id)
            if not success:
                return JSONResponse(
                    {"error": f"Note '{note_id}' not found"}, status_code=404
                )
            return JSONResponse(
                {"status": "ok", "message": f"Note '{note_id}' deleted"}
            )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/notes/{project_id}", methods=["GET"])
async def notes_list(request: Request):
    """List all knowledge notes."""
    project_id = request.path_params["project_id"]
    try:
        from src.trellis.knowledge_graph import NoteGraph

        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        notes = [
            {
                "id": n.id,
                "title": n.title,
                "tags": n.tags,
                "updated": n.updated_at,
            }
            for n in graph.notes.values()
        ]
        return JSONResponse({"notes": notes, "count": len(notes)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/feature/{project_id}/context/{symbol}", methods=["GET"])
async def feature_context(request: Request):
    """Get feature context for a symbol."""
    project_id = request.path_params["project_id"]
    symbol = request.path_params["symbol"]
    try:
        bridge = _get_bridge(project_id)
        context = bridge.get_feature_context(symbol)
        if context:
            return JSONResponse(context)
        return JSONResponse({"error": "No feature context found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/feature/{project_id}/divergence/{symbol}", methods=["GET"])
async def feature_divergence(request: Request):
    """Check feature divergence for a symbol."""
    project_id = request.path_params["project_id"]
    symbol = request.path_params["symbol"]
    try:
        bridge = _get_bridge(project_id)
        warnings = bridge.check_feature_divergence(symbol)
        return JSONResponse(
            {
                "symbol": symbol,
                "divergence_warnings": warnings,
                "has_divergence": len(warnings) > 0,
            }
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/projects", methods=["GET"])
async def list_projects(request: Request):
    """List synced projects with graph data.

    Projects register themselves in ~/.trellis/projects/<id>/project.json
    when synced; the code graph itself lives in each project's own
    .code-graph directory. Legacy entries with a centralized .code-graph
    are still listed.
    """
    from src.trellis.utils import get_trellis_data_dir

    projects = []

    trellis_data = get_trellis_data_dir()
    projects_dir = trellis_data / "projects"
    if projects_dir.exists():
        for item in projects_dir.iterdir():
            if not item.is_dir():
                continue
            marker = item / "project.json"
            if marker.exists():
                try:
                    repo_path = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])
                except (json.JSONDecodeError, KeyError, OSError):
                    continue
                if (repo_path / ".code-graph" / "index.db").exists():
                    projects.append(
                        {"id": item.name, "name": item.name, "path": str(repo_path)}
                    )
                continue
            # Legacy: centralized .code-graph stored under the data dir
            code_graph_dir = item / ".code-graph"
            if code_graph_dir.exists() and (code_graph_dir / "index.db").exists():
                projects.append(
                    {
                        "id": item.name,
                        "name": item.name,
                        "path": str(item),
                    }
                )

    return JSONResponse({"projects": projects})


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Launcher and browser auto-open are only for bundled (PyInstaller) releases.
    # In development, or when used as an MCP server by OpenCode/Claude/etc.,
    # we never want to force HTTP mode or open a browser.
    if getattr(sys, "frozen", False):
        try:
            from src.trellis.launcher import setup_environment, open_browser

            setup_environment()
            # Browser auto-open only makes sense for the standalone HTTP app.
            # When an MCP client spawns the exe over stdio (TRELLIS_TRANSPORT=stdio
            # preset in its env), opening a browser on every session would be wrong.
            if os.environ.get("TRELLIS_TRANSPORT") == "http":
                open_browser()
        except ImportError:
            pass

    # Determine transport from environment; default to stdio for MCP servers.
    transport = os.environ.get("TRELLIS_TRANSPORT", "stdio")

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # HTTP mode - serve REST endpoints, visualizer, and MCP over HTTP
        # mcp.http_app() includes the custom routes above plus the MCP protocol
        # endpoint at /mcp, so AI agents can connect via HTTP or stdio.
        import uvicorn

        uvicorn.run(
            _LocalHttpGuard(mcp.http_app()),
            host=os.environ.get("TRELLIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("TRELLIS_PORT", "17317")),
        )
