"""FastMCP tool server for Trellis.

Builds the FastMCP instance exposing every trellis_* MCP tool plus the
trellis://skill resource. All shared state lives in src.trellis.core; this
module only registers tools and formats their responses.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from src.trellis import core, usage

# Repo root (this file lives at src/trellis/mcp_server.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# trellis_analyze_diff is disabled by default: broad diffs run per-function
# impact analysis (subprocess per function) and time out. Opt in explicitly.
DIFF_ANALYSIS_ENABLED = os.environ.get("TRELLIS_ENABLE_DIFF_ANALYSIS", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# trellis_sync is operator-only by default: a first index takes 5-15 minutes
# and hammers the repo, so agents shouldn't trigger it. Indexing happens via
# the headless CLI (`trellis sync`); agents monitor with trellis_sync_status.
# Opt back in with TRELLIS_ENABLE_MCP_SYNC=1.
SYNC_TOOL_ENABLED = os.environ.get("TRELLIS_ENABLE_MCP_SYNC", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
MAX_DIFF_CHARS = int(os.environ.get("TRELLIS_MAX_DIFF_CHARS", "200000"))
MAX_DIFF_FILES = int(os.environ.get("TRELLIS_MAX_DIFF_FILES", "50"))
MAX_DIFF_FUNCTIONS = int(os.environ.get("TRELLIS_MAX_DIFF_FUNCTIONS", "25"))

_SERVER_INSTRUCTIONS = """\
Trellis: graph-based code analysis and knowledge management for this workspace.

Getting started:
1. Projects are indexed by the operator, not by you: trellis_sync is not exposed as a tool. Before working on a project, call trellis_sync_status(project_id=...). If there is no index yet, ask the operator to index it: `trellis sync <repo_path> --project-id <id>` (headless CLI). The index then stays fresh automatically — the graph server watches the repo and re-indexes edited files on its own. Re-indexing is needed only after large external changes (big pulls, branch switches).
2. Check the project_md status (trellis_sync_status reports it). If "missing", create <repo root>/project.md BEFORE any feature-level work. Format (parsed strictly): one "## Feature: <Name>" section per feature, containing a plain-text description paragraph, then "### Decisions" ("- DEC-001: decision (because: rationale)" with nested "- Constraint: ..."), "### Files" (glob patterns, one per line, e.g. src/auth/**), and "### Dependencies" ("- Feature: <Other Feature>"). Other layouts (tables, "- **Description**:" bullets) are ignored.
3. Explore with trellis_list_modules and trellis_search_code; inspect symbols with trellis_get_function; understand features with trellis_feature_info.
4. Before modifying any function, run trellis_analyze_impact on it. The graph reflects your edits automatically — just re-analyze.
5. Document decisions and feature architecture with trellis_create_note (wiki links [[Note]], code mentions @function).

Long-running calls:
- Tool calls run ASYNC on the server: a long call won't block other projects' trellis calls, but each call returns ONLY when its work is done, and calls on the SAME project queue one at a time.
- Analysis tools on very large projects (trellis_get_graph, trellis_tour, trellis_project_health) can take a minute or more — run them in BACKGROUND mode (your harness's run_in_background/async option) and keep working; the harness notifies you when the result is ready. Never poll in a loop, interrupt, cancel, or re-issue a pending call — it is still running server-side, and duplicates queue behind it (MCP error -32001).
- If a call fails with "not indexed" or an empty graph, the project has no index: ask the operator to run `trellis sync` (see step 1) and check trellis_sync_status until it reports completed.

The full skill document — complete workflow, project.md template, and tool reference — is available as the MCP resource "trellis://skill". Fetch it if the trellis-mcp skill is not installed in your agent.
"""

_SKILL_FALLBACK = """\
# Trellis MCP — Getting Started (bundled fallback)

1. Projects are indexed by the operator (trellis_sync is not a tool here). Check trellis_sync_status(project_id=...) first; if there is no index, ask the operator to run `trellis sync <repo_path> --project-id <id>` and wait for trellis_sync_status to report completed.
2. If project_md is "missing", create <repo root>/project.md before feature-level work:
   one "## Feature: <Name>" section per feature with a plain-text description,
   "### Decisions" (- DEC-001: text (because: rationale)), "### Files" (glob patterns,
   one per line), "### Dependencies" (- Feature: <Name>).
3. Explore: trellis_list_modules, trellis_search_code, trellis_get_function, trellis_feature_info.
4. Before changing code: trellis_analyze_impact. The graph re-indexes edits automatically (file watcher) — just re-analyze.
5. Document with trellis_create_note ([[wiki links]] and @code mentions).

Long-running calls: calls run ASYNC on the server (a long call won't block other projects), but each returns only when its work finishes — never fire-and-forget. Big analysis calls (trellis_get_graph, trellis_tour, trellis_project_health) can take a minute or more: run them in BACKGROUND mode, keep working, and act on the notification. Never poll, interrupt, or re-issue a pending call (MCP error -32001). If a project has no index, ask the operator to `trellis sync` it; monitor with trellis_sync_status.

Install the trellis-mcp skill (skills/ folder in the Trellis distribution) for the full workflow.
"""


def _load_skill_content() -> str:
    """Read the bundled SKILL.md; fall back to a built-in getting-started summary."""
    candidates = [_REPO_ROOT / "skills" / "trellis-mcp" / "SKILL.md"]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).parent / "skills" / "trellis-mcp" / "SKILL.md")
    for path in candidates:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return _SKILL_FALLBACK


class _UsageRecordingMiddleware(Middleware):
    """FastMCP middleware: time every tool call and record it via usage.py.

    Attached once to the server, so every @mcp.tool() invocation is timed and
    persisted without touching the tool bodies. Tool exceptions are recorded
    as failures and re-raised; recording failures are swallowed so telemetry
    can never break a tool call.
    """

    async def on_call_tool(self, context, call_next):
        start = time.perf_counter()
        message = context.message
        name = getattr(message, "name", "") or ""
        arguments = getattr(message, "arguments", None) or {}
        project_id = arguments.get("project_id", "") if isinstance(arguments, dict) else ""
        ok = True
        try:
            result = await call_next(context)
            ok = not bool(getattr(result, "isError", False))
            return result
        except Exception:
            ok = False
            raise
        finally:
            try:
                # File I/O: keep it off the event loop.
                await asyncio.to_thread(
                    usage.record_call,
                    name,
                    project_id,
                    ok,
                    (time.perf_counter() - start) * 1000,
                )
            except Exception:
                pass



def _module_overview(bridge, module_path: str) -> Dict[str, Any]:
    """Symbols per file under a module path, and its top outgoing edges.

    Runs in a thread. Paths starting with module_path win; a substring match
    is the fallback. LIKE wildcards in the path are escaped.
    """
    from src.trellis import graph_queries as gq

    max_files, max_symbols = 50, 50
    bridge.ensure_fresh()
    db_path = bridge.code_graph_path / "index.db"
    if not db_path.exists():
        return {"error": "No code-graph database found. Ask the operator to run `trellis sync`."}

    prefix = module_path.strip().replace("\\", "/").strip("/")
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    with gq.connect(db_path) as conn:
        # The path itself (a file) or anything below it (a directory).
        cond, params, match = "(f.path = ? OR f.path LIKE ? ESCAPE '!')", (prefix, f"{escaped}/%"), "prefix"
        if not prefix:
            cond, params = "1", ()
        elif not conn.execute(f"SELECT 1 FROM files f WHERE {cond} LIMIT 1", params).fetchone():
            cond, params, match = "f.path LIKE ? ESCAPE '!'", (f"%{escaped}%",), "substring"
        rows = conn.execute(
            f"""
            SELECT f.path, n.type, n.name, n.qualified_name, n.start_line
            FROM nodes n JOIN files f ON n.file_id = f.id
            WHERE {cond} AND n.type IN ('function', 'method', 'class')
            ORDER BY f.path, n.start_line
            """,
            params,
        ).fetchall()
        source_cond = cond.replace("f.path", "sf.path")
        target_cond = cond.replace("f.path", "tf.path")
        outgoing = conn.execute(
            f"""
            SELECT tf.path, e.relation, COUNT(*) AS cnt
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            JOIN nodes t ON e.target_id = t.id
            JOIN files sf ON s.file_id = sf.id
            JOIN files tf ON t.file_id = tf.id
            WHERE {source_cond} AND NOT {target_cond}
              AND e.relation IN ('calls', 'imports') {gq.confident_filter(conn)}
            GROUP BY tf.path, e.relation
            ORDER BY cnt DESC
            LIMIT 30
            """,
            params + params,
        ).fetchall()

    files: Dict[str, List[str]] = {}
    truncated = False
    for path, kind, name, qname, line in rows:
        if len(files) >= max_files and path not in files:
            truncated = True
            continue
        symbols = files.setdefault(path, [])
        if len(symbols) >= max_symbols:
            truncated = True
            continue
        symbols.append(f"{line} {kind} {qname or name}")
    if not files:
        return {"module_path": module_path, "error": f"No indexed files match '{module_path}'"}
    return {
        "module_path": module_path,
        "match": match,
        "file_count": len({r[0] for r in rows}),
        "files": files,
        "outgoing_edges": [f"{path} {relation} x{count}" for path, relation, count in outgoing],
        "truncated": truncated,
    }


def _trace_path(bridge, db_path, from_feature: str, to_feature: str) -> Dict[str, Any]:
    """Call/import edges between two features or module paths (runs in a thread).

    File sets go into temp tables, so large features don't hit SQLite's
    bound-parameter limit. Upstream 'ambiguous' by-name edges are left out.
    """
    import sqlite3
    from contextlib import closing

    from src.trellis.feature_impact import ProjectContextParser
    from src.trellis.graph_queries import confident_filter

    context = ProjectContextParser(str(bridge.project_path))
    with closing(sqlite3.connect(str(db_path), timeout=10)) as conn:
        all_paths = [row[0] for row in conn.execute("SELECT path FROM files")]

        def files_for(name):
            """Files of a project.md feature, else paths containing `name`."""
            if name in context.features:
                return set(context.files_for_feature(name, all_paths))
            needle = name.lower()
            return {path for path in all_paths if needle in path.lower()}

        from_files, to_files = files_for(from_feature), files_for(to_feature)
        if not from_files or not to_files:
            missing = from_feature if not from_files else to_feature
            return {"error": f"Feature/module '{missing}' not found"}

        for table, paths in (("t_from", from_files), ("t_to", to_files)):
            conn.execute(f"CREATE TEMP TABLE {table} (path TEXT PRIMARY KEY)")
            conn.executemany(f"INSERT INTO {table} VALUES (?)", ((p,) for p in paths))
        confident = confident_filter(conn)

        def edges(where: str, limit: int) -> List[str]:
            rows = conn.execute(
                f"""
                SELECT DISTINCT sf.path, tf.path, s.name, e.relation, t.name
                FROM edges e
                JOIN nodes s ON e.source_id = s.id
                JOIN nodes t ON e.target_id = t.id
                JOIN files sf ON s.file_id = sf.id
                JOIN files tf ON t.file_id = tf.id
                WHERE e.relation IN ('calls', 'imports') {confident} AND {where}
                  AND sf.path != tf.path
                LIMIT {limit}
                """
            )
            # 'src_file:caller -> dst_file:callee (relation)'; <module> = file level.
            return [f"{r[0]}:{r[2]} -> {r[1]}:{r[4]} ({r[3]})" for r in rows]

        in_from = "sf.path IN (SELECT path FROM t_from)"
        to_in = "tf.path IN (SELECT path FROM t_to)"
        outside = "NOT IN (SELECT path FROM t_from UNION SELECT path FROM t_to)"
        direct = edges(f"{in_from} AND {to_in}", 20)
        outbound = edges(f"{in_from} AND tf.path {outside}", 10)
        inbound = edges(f"sf.path {outside} AND {to_in}", 10)

    return {
        "from_feature": from_feature,
        "to_feature": to_feature,
        "from_files_count": len(from_files),
        "to_files_count": len(to_files),
        "from_files_sample": sorted(from_files)[:5],
        "to_files_sample": sorted(to_files)[:5],
        "shared_files": sorted(from_files & to_files)[:10],
        "direct_connections": direct,
        "direct_connection_count": len(direct),
        "direct_connections_truncated": len(direct) >= 20,
        "outbound_intermediate": outbound,
        "inbound_intermediate": inbound,
    }


def _symbol_results(results) -> Dict[str, Any]:
    """Search hits as compact lines; an engine error is passed through."""
    rows = [r for r in results or [] if isinstance(r, dict)]
    errors = [r["error"] for r in rows if r.get("error")]
    if errors and len(errors) == len(rows):
        return {"error": errors[0]}
    return {"results": [core.symbol_line(r) for r in rows if not r.get("error")]}


def create_mcp_server(version: str) -> FastMCP:
    """Build the FastMCP server with every Trellis tool registered."""
    # Aliases keep the tool bodies byte-identical to their original home.
    _dump = core.dump_json
    _truncate = core.truncate
    _project_md_status = core.project_md_status
    _module_entry = core.module_entry
    _get_bridge = core.get_bridge
    _resolve_project_path = core.resolve_project_path
    _sync_jobs = core.sync_jobs
    _new_sync_job = core.new_sync_job
    _set_sync_job = core.set_sync_job
    _now_iso = core.now_iso
    _project_sync_summary = core.project_sync_summary
    MAX_CODE_CONTENT_CHARS = core.MAX_CODE_CONTENT_CHARS
    MAX_NOTE_CONTENT_CHARS = core.MAX_NOTE_CONTENT_CHARS
    MAX_IMPACT_CALLERS = 50

    mcp = FastMCP(
        "trellis-core",
        version=version,
        instructions=_SERVER_INSTRUCTIONS,
        strict_input_validation=True,
        mask_error_details=True,
    )
    mcp.add_middleware(_UsageRecordingMiddleware())

    # ------------------------------------------------------------------
    # Skill resource (bootstrap for agents without the trellis-mcp skill)
    # ------------------------------------------------------------------

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

    # trellis_sync is operator-only by default (SYNC_TOOL_ENABLED): a first
    # index takes 5-15 minutes, so agents must not trigger it — the operator
    # indexes via `trellis sync` and agents watch trellis_sync_status.
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

        Duration: the initial sync of a large repo takes SEVERAL MINUTES
        (5-15 min for a big codebase) — this is normal, not a hang. This call
        runs ASYNC on the server: it won't block other projects' trellis calls,
        it returns ONLY when the sync finishes (it is not fire-and-forget), and
        other calls on the same project queue behind it.

        How to run it: invoke this tool in BACKGROUND mode (your harness's
        run_in_background / async tool-call option) and continue other work —
        do not sit blocked on this call and do not let it stall the rest of your
        task. The harness sends a system notification when the result is ready,
        and you pick it up then. NEVER poll in a loop, interrupt, cancel, or
        re-issue the call while it is pending: it is still running server-side,
        and a duplicate sync queues behind it and only makes that project's calls
        slower.

        If your client's timeout is shorter than the sync, run the trellis
        executable directly in a background shell instead:
        `trellis.exe sync <repo_path> --project-id <id>` (same exe your MCP
        config uses) — it prints progress and exits when done; then continue
        with trellis_list_modules.

        To check progress — while waiting, after a timeout, or for a headless
        CLI sync — call trellis_sync_status(project_id=...): it reports
        running/completed/failed plus index size for both MCP and CLI syncs.
        While it says running, keep waiting; do not re-issue this tool.
        """
        cache_key = repo_path or project_id
        job_key = project_id or cache_key
        job: dict = {}
        try:
            # Clear bridge cache for this project to avoid DB lock conflicts
            await asyncio.to_thread(core.evict_bridge, cache_key)

            # trellis_sync is the registration path: it may point at a repo that
            # has never been synced. All other tools require a registered project.
            from src.trellis import CodeGraphBridge

            resolved = _resolve_project_path(cache_key, allow_unregistered=True)

            # Register the agent's project_id -> resolved repo so later calls
            # with the same id resolve via the registry instead of falling into
            # the unknown-id error.
            if project_id:
                from src.trellis.utils import register_project

                register_project(project_id, resolved)

            job = _new_sync_job("mcp")
            _set_sync_job(job_key, job)

            bridge = CodeGraphBridge(resolved)

            # Trigger actual indexing
            if incremental:
                sync_result = await asyncio.to_thread(bridge.incremental_sync)
            else:
                sync_result = await asyncio.to_thread(bridge.sync_project)

            # After sync, we need a fresh bridge since the old one closed its process
            await asyncio.to_thread(core.evict_bridge, cache_key)

            # Get fresh bridge for health check
            bridge = _get_bridge(cache_key)
            health = await asyncio.to_thread(bridge.health_check)

            job["state"] = "completed"
            job["finished_at"] = _now_iso()
            job["nodes"] = health.get("nodes_count", 0)
            job["files"] = health.get("files_count", 0)
            _set_sync_job(job_key, job)

            # Materialize/update the per-feature notes from project.md so the
            # doc graph stays connected. Agents only maintain project.md.
            try:
                from src.trellis.spec_note_sync import sync_feature_notes

                notes_sync = await asyncio.to_thread(sync_feature_notes, str(bridge.project_path))
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
            if job.get("state") == "running":
                job["state"] = "failed"
                job["finished_at"] = _now_iso()
                job["detail"] = str(e)
                _set_sync_job(job_key, job)
            return _dump({"error": str(e)})
        finally:
            # A client timeout cancels the coroutine (CancelledError is not caught
            # above): the to_thread sync keeps running in its worker thread, but
            # from the handler's perspective the job is dead. Record that so
            # trellis_sync_status doesn't report a zombie "running" forever —
            # an approximation, since the thread may still finish the index fine.
            if (
                job.get("state") == "running"
                and _sync_jobs.get(job_key) is job
            ):
                job["state"] = "failed"
                job["finished_at"] = _now_iso()
                job["detail"] = "sync handler cancelled (client timeout or interrupt)"
                _set_sync_job(job_key, job)

    if SYNC_TOOL_ENABLED:
        mcp.tool()(trellis_sync)

    @mcp.tool()
    async def trellis_sync_status(
        project_id: str = "",
    ) -> str:
        """Check whether a project is indexed and whether a sync is running.

        Call this first on a project. Covers syncs the operator runs with the
        CLI (`trellis sync <repo_path> --project-id <id>`). While it says
        "running", wait and check again; "completed" means the index is ready.

        Returns the repo path, whether the index exists, node/file counts,
        the last sync state, project_md status and a one-sentence guidance
        field. With no project_id, a summary for every registered project.

        Example:
          trellis_sync_status(project_id='my-project')
        """
        try:
            if project_id:
                summary = await asyncio.to_thread(_project_sync_summary, project_id)
                return _dump(summary)

            from src.trellis.utils import list_registered_projects

            registered = await asyncio.to_thread(list_registered_projects)
            projects = []
            for pid in registered:
                summary = await asyncio.to_thread(_project_sync_summary, pid)
                summary.pop("repo_path", None)
                summary.pop("guidance", None)
                projects.append(summary)
            return _dump({"projects": projects})
        except Exception as e:
            return _dump({"error": str(e)})

    @mcp.tool()
    async def trellis_module_overview(
        project_id: str = "",
        module_path: str = "",
    ) -> str:
        """Get a code module overview (symbols, files, dependencies).

        Use this for inspecting a directory or module in the codebase.
        module_path is a directory or file path from the repo root (a
        substring of a path is used only when nothing starts with it).
        files maps each file to 'line kind name' entries; outgoing_edges
        read 'target_file relation xN'.
        For feature-level documentation and decisions, use trellis_feature_info.

        Example:
          trellis_module_overview(project_id='tui.image-editor', module_path='apps/image-editor/src/js/component')
        """
        try:
            bridge = _get_bridge(project_id)
            return _dump(await asyncio.to_thread(_module_overview, bridge, module_path))
        except Exception as e:
            return _dump({"error": str(e)})

    @mcp.tool()
    async def trellis_analyze_impact(
        project_id: str = "",
        function_path: str = "",
        depth_mode: str = "standard",
    ) -> str:
        """Analyze impact of changing a function: who calls it, and which
        project.md features, decisions and constraints that touches.

        function_path: 'name', 'Class.method', 'path/to/file.py:function' or
        'path/to/file.py:Class.method'. Use the file form when the name is
        defined in more than one place (other_definitions lists the rest).

        callers are confident call sites in non-test code; test_callers come
        from test files; ambiguous_callers are by-name guesses that may point
        at a same-named function elsewhere. Risk is computed from callers
        (from ambiguous ones only when there are no others; see risk_note).
        A class covers callers of the class and its methods.

        depth_mode: 'standard' (direct callers) or 'deep' (also callers of
        callers, up to 3 levels, in indirect_callers).

        Example:
          trellis_analyze_impact(project_id='my-api', function_path='services/billing.py:charge')
        """
        def _brief(entries, cap):
            return [f"{c['file_path']}:{c['line']} {c['qualified_name']}" for c in entries[:cap]]

        try:
            bridge = _get_bridge(project_id)
            depth = 3 if depth_mode == "deep" else 1
            report = await asyncio.to_thread(bridge.get_feature_impact, function_path, depth=depth)
            if not report.get("found", True):
                return _dump({k: report[k] for k in ("symbol", "error", "hint")})

            callers = report.get("callers", [])
            tests = report.get("test_callers", [])
            ambiguous = report.get("ambiguous_callers", [])
            indirect = report.get("indirect_callers", [])
            result = {
                "symbol": function_path,
                "resolved": report.get("resolved"),
                "other_definitions": report.get("other_definitions", []),
                "risk_level": report.get("risk_level", "unknown"),
                "risk_note": report.get("risk_note") or None,
                "caller_count": len(callers),
                "callers": _brief(callers, MAX_IMPACT_CALLERS),
                "callers_truncated": len(callers) > MAX_IMPACT_CALLERS,
                "test_caller_count": len(tests),
                "test_callers": _brief(tests, 10),
                "ambiguous_caller_count": len(ambiguous),
                "ambiguous_callers": _brief(ambiguous, 10),
                "indirect_caller_count": len(indirect),
                "indirect_callers": [
                    f"depth {c['depth']}: {c['file_path']}:{c['line']} {c['qualified_name']}"
                    for c in indirect[:MAX_IMPACT_CALLERS]
                ],
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
                    if fi["feature_name"] != "unassigned"
                ],
                # Root and affected functions in files no project.md feature covers.
                "unmapped_functions": sum(
                    len(fi["impacted_functions"])
                    for fi in report.get("feature_impacts", [])
                    if fi["feature_name"] == "unassigned"
                ),
                "development_pointers": report.get("development_pointers", [])[:10],
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
        """Get details (location, signature, source) for a function or class.

        function_path can be:
        - Just the function name: "create_llm"
        - Qualified name: "AIFactory.create_llm"
        - File:function format: "backend/ai/factory.py:create_llm"
        - Dotted module path: "backend.ai.factory.create_llm"
        When several definitions match, the best is returned and the rest
        are listed in other_definitions.

        For functions and methods it also returns callees (project functions
        it calls) and callers (confident, non-test), as 'path:line name'
        lines, so one call covers source and neighbours. Use
        trellis_analyze_impact for the full caller set and feature impact.
        """
        from src.trellis import graph_queries as gq

        def _lines(entries, cap):
            return [f"{c['file_path']}:{c['line']} {c['qualified_name']}" for c in entries[:cap]]

        def _lookup(bridge):
            bridge.ensure_fresh()
            node = bridge.lookup_symbol(function_path)
            file_part, name = gq.split_symbol(function_path)
            if node is None:
                # Not a function/class in the index (a constant, interface,
                # type...): ask upstream, and keep only a hit in the named file.
                node = bridge.upstream_ast_node(name, file_path=file_part or None)
                found_in = (node or {}).get("file_path", "") if isinstance(node, dict) else ""
                if file_part and not (found_in and gq.path_matches(found_in, file_part)):
                    node = None
            if not isinstance(node, dict) or not node.get("name"):
                return {"error": f"Function '{function_path}' not found in index"}
            return node

        try:
            bridge = _get_bridge(project_id)
            node = await asyncio.to_thread(_lookup, bridge)

            # Cap source payload so large functions don't flood the context
            if isinstance(node, dict) and isinstance(node.get("code_content"), str):
                node["code_content"] = _truncate(
                    node["code_content"], MAX_CODE_CONTENT_CHARS
                )
            node.pop("source", None)
            if isinstance(node.get("callers"), list):
                callers, callees = node.pop("callers"), node.pop("callees", [])
                node["caller_count"] = len(callers)
                node["callers"] = _lines(callers, 15)
                node["callees"] = _lines(callees, 30)
                if len(callees) > 30:
                    node["callees_truncated"] = True

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

        Each result reads 'path:start-end type name(signature)'; read the
        source with trellis_get_function('path:name').
        For searching knowledge notes, use trellis_search_notes.

        Example:
          trellis_search_code(project_id='tui.image-editor', query='addIcon', limit=5)
        """
        try:
            bridge = _get_bridge(project_id)
            results = await asyncio.to_thread(bridge.search, query, limit=limit)
            return _dump(_symbol_results(results))
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
            results = await asyncio.to_thread(
                bridge.ast_search,
                query=query or None,
                node_type=node_type or None,
                returns=returns or None,
                params=params or None,
                limit=limit,
            )
            return _dump(_symbol_results(results))
        except Exception as e:
            return _dump({"error": str(e)})

    @mcp.tool()
    async def trellis_list_modules(
        project_id: str = "",
    ) -> str:
        """List all code modules/directories in the project with symbol counts.

        Each entry reads 'path: N files, M symbols (languages)'.
        For project.md feature specifications, use trellis_feature_info.
        """
        try:
            bridge = _get_bridge(project_id)
            pmap = await asyncio.to_thread(bridge.project_map)
            modules = [_module_entry(m) for m in pmap.get("modules", [])]
            return _dump(
                {
                    "module_count": len(modules),
                    "modules": [
                        f"{m['path']}: {m['files']} files, {m['symbols']} symbols"
                        + (f" ({m['language']})" if m["language"] else "")
                        for m in modules
                    ],
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
            info = await asyncio.to_thread(bridge.get_feature_info, feature_name)

            # Cap note payload so long architecture notes don't flood the context
            note = info.get("note")
            if isinstance(note, dict) and isinstance(note.get("content"), str):
                note["content"] = _truncate(note["content"], MAX_NOTE_CONTENT_CHARS)
            if isinstance(note, dict):
                note.pop("related_notes", None)  # same list as info["related_notes"]

            # 'path:line qualified_name' lines instead of row objects.
            info["functions_sample"] = [
                f"{f['file_path']}:{f.get('start_line')} {f.get('qualified_name') or f.get('name')}"
                for f in info.get("functions_sample", [])
            ]
            info["hot_functions"] = [
                f"{f['file_path']}:{f.get('start_line')} {f.get('qualified_name') or f.get('name')}"
                f" ({f.get('caller_count', 0)} callers)"
                for f in info.get("hot_functions", [])
            ]

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
        call/import edges between them, as 'src_file:caller -> dst_file:callee
        (relation)' lines.

        Example:
          trellis_trace_path(project_id='tui.image-editor', from_feature='Icons', to_feature='Graphics')
        """
        try:
            bridge = _get_bridge(project_id)
            db_path = bridge.code_graph_path / "index.db"
            if not db_path.exists():
                return _dump(
                    {"error": "No code-graph database found. Run trellis sync first."}
                )

            return _dump(await asyncio.to_thread(_trace_path, bridge, db_path, from_feature, to_feature))
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
            graph = await asyncio.to_thread(bridge.get_graph_for_visualizer, max_nodes=max_nodes)
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
        Each line reads 'path [role] uses: ... | used by N | key: ...', with
        '(in import cycle)' when it is in one. Modules with no symbols and no
        imports either way are counted in skipped_empty, not listed.

        Use when onboarding to a codebase or exploring an unfamiliar module.

        Example:
          trellis_tour(project_id='tui.image-editor')
          trellis_tour(project_id='tui.image-editor', path='apps/image-editor/src/js')
        """
        try:
            bridge = _get_bridge(project_id)
            result = await asyncio.to_thread(bridge.tour, path or None)
            order = result.get("reading_order") if isinstance(result, dict) else None
            if not isinstance(order, list):
                return _dump(result)
            lines, skipped = [], 0
            for m in order:
                uses, used_by, key = m.get("depends_on") or [], m.get("depended_on_by") or 0, m.get("key_symbols") or []
                if not uses and not used_by and not key:
                    skipped += 1
                    continue
                parts = [f"{m.get('path')} [{m.get('role', '?')}]"]
                if uses:
                    parts.append("uses: " + ", ".join(uses))
                if used_by:
                    parts.append(f"used by {used_by}")
                if key:
                    parts.append("key: " + ", ".join(key[:6]))
                lines.append(" | ".join(parts) + (" (in import cycle)" if m.get("in_cycle") else ""))
            return _dump({"reading_order": lines, "skipped_empty": skipped})
        except Exception as e:
            return _dump({"error": str(e)})

    @mcp.tool()
    async def trellis_trace_http_route(
        project_id: str = "",
        route_path: str = "",
        depth: int = 3,
    ) -> str:
        """Trace an HTTP route to its handler and downstream calls (web projects).

        Given a URL like 'GET /api/users/12/' or a route pattern like
        '/api/users/<pk>/', returns the handler(s) and their downstream call
        chain (capped at depth). Django and Django REST Framework (urls.py,
        include(), as_view(), routers, @action) are traced by Trellis; an empty
        route_path lists all Django routes. FastAPI/Flask/Express use the
        upstream tracer. Projects with no routes return an empty result.

        Example:
          trellis_trace_http_route(project_id='my-api', route_path='POST /api/login')
        """
        try:
            bridge = _get_bridge(project_id)
            result = await asyncio.to_thread(bridge.trace_http_route, route_path, depth=depth)
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
        from src.trellis import graph_queries as gq
        from src.trellis.utils import is_test_path

        def _chokepoint(c, conn):
            # The engine's caller count includes by-name guesses and tests;
            # report the confident production count beside it.
            engine = c.get("caller_count", 0)
            callers = f"{engine} callers"
            if conn is not None:
                confident = gq.named_caller_count(conn, c.get("file_path"), c.get("name"), c.get("type"))
                if confident != engine:
                    callers = f"{confident} callers; engine counts {engine} incl. by-name guesses and tests"
            return (
                f"{c.get('file_path')}: {c.get('name')} ({c.get('type')}, "
                f"betweenness {round(c.get('normalized') or 0, 4)}, {callers})"
            )

        def _health(bridge):
            # Ask for extra rows: test code the engine does not recognise
            # (Django's tests.py) is dropped here.
            raw = bridge.project_health(limit * 2)
            rows = [
                c
                for c in raw.get("chokepoints") or []
                if isinstance(c, dict) and not is_test_path(c.get("file_path") or "")
            ][:limit]
            db_path = getattr(bridge, "code_graph_path", None)
            db_path = db_path / "index.db" if db_path else None
            if db_path and db_path.exists():
                with gq.connect(db_path) as conn:
                    chokepoints = [_chokepoint(c, conn) for c in rows]
            else:
                chokepoints = [_chokepoint(c, None) for c in rows]
            cycles = [
                {
                    "loop": " -> ".join(c.get("cycle") or []),
                    "size": c.get("size") or len(c.get("files") or []),
                    "files_sample": (c.get("files") or [])[:8],
                }
                for c in raw.get("import_cycles") or []
                if isinstance(c, dict)
            ]
            surprising = [
                f"{s.get('source_file')}:{s.get('source')} -> {s.get('target_file')}:{s.get('target')}"
                f" ({s.get('relation')}, {s.get('confidence')}): {'; '.join(s.get('why') or [])}"
                for s in raw.get("surprising_connections") or []
                if isinstance(s, dict)
                and not is_test_path(s.get("source_file") or "")
                and not is_test_path(s.get("target_file") or "")
            ][:limit]
            return {
                "chokepoints": chokepoints,
                "import_cycle_count": len(cycles),
                "import_cycles": cycles[:limit],
                "surprising_connections": surprising,
            }

        try:
            bridge = _get_bridge(project_id)
            return _dump(await asyncio.to_thread(_health, bridge))
        except Exception as e:
            return _dump({"error": str(e)})

    @mcp.tool()
    async def trellis_detect_hotspots(
        project_id: str = "",
        limit: int = 20,
    ) -> str:
        """Find the most-used functions (potential hotspots).

        Ranks non-test functions by how many distinct functions call them or
        modules import them, from non-test code. Upstream by-name guesses
        ('ambiguous' edges) are left out.
        """
        from src.trellis import graph_queries as gq

        def _hotspots(bridge):
            bridge.ensure_fresh()
            db_path = bridge.code_graph_path / "index.db"
            if not db_path.exists():
                return {"error": "No code-graph database found. Run trellis sync first."}
            with gq.connect(db_path) as conn:
                return {"hotspots": gq.hotspots(conn, limit)}

        try:
            bridge = _get_bridge(project_id)
            return _dump(await asyncio.to_thread(_hotspots, bridge))
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

            # Step 3: Query SQLite to find affected functions. Refresh first so
            # functions added or moved by this diff are in the index.
            bridge = _get_bridge(project_id)
            await asyncio.to_thread(bridge.ensure_fresh)
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
            impact_results = []
            analyzed_symbols = set()
            functions_capped = False

            for func in affected_functions:
                symbol = func["qualified_name"] or func["name"]
                key = (func["file_path"], symbol)
                if key in analyzed_symbols:
                    continue
                if len(analyzed_symbols) >= MAX_DIFF_FUNCTIONS:
                    functions_capped = True
                    break
                analyzed_symbols.add(key)

                try:
                    report = await asyncio.to_thread(
                        bridge.get_feature_impact,
                        f"{func['file_path']}:{symbol}",
                        refresh=False,
                    )
                    if not report.get("found", True):
                        raise LookupError(report.get("error", "not found"))
                    impact_results.append(
                        {
                            "symbol": symbol,
                            "file_path": func["file_path"],
                            "type": func["type"],
                            "changed_lines": func["changed_lines"],
                            "risk_level": report.get("risk_level", "unknown"),
                            "caller_count": report.get("caller_count", 0),
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
                    risk_scores.get(str(r["risk_level"]).lower(), 0)
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
        """Get module boundary map: modules and cross-module import counts.

        dependencies read 'from_module -> to_module (N imports)', most
        imports first.
        """
        try:
            bridge = _get_bridge(project_id)
            pmap = await asyncio.to_thread(bridge.project_map)
            modules = pmap.get("modules", [])
            # Engine key is module_dependencies; the text-map parser says dependencies.
            deps = pmap.get("module_dependencies") or pmap.get("dependencies") or []
            deps = sorted(
                (d for d in deps if d.get("from") and d.get("to")),
                key=lambda d: -(d.get("imports") or d.get("count") or 0),
            )
            cap = 150
            return _dump(
                {
                    "modules": [m.get("path") for m in modules],
                    "dependency_count": len(deps),
                    "dependencies": [
                        f"{d['from']} -> {d['to']} ({d.get('imports') or d.get('count') or 0} imports)"
                        for d in deps[:cap]
                    ],
                    "dependencies_truncated": len(deps) > cap,
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
            graph = await asyncio.to_thread(NoteGraph, resolved)

            # Omitted tags keep the note's current tags on update.
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
            note = await asyncio.to_thread(
                graph.save_note, note_id, content, title=title, tags=tag_list
            )

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
            graph = await asyncio.to_thread(NoteGraph, resolved)
            note = await asyncio.to_thread(graph.get_note, note_id)

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
                    "backlinks": await asyncio.to_thread(graph.get_backlinks, note_id),
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
            graph = await asyncio.to_thread(NoteGraph, resolved)
            results = await asyncio.to_thread(graph.search_notes, query)

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
            graph = await asyncio.to_thread(NoteGraph, resolved)
            success = await asyncio.to_thread(graph.delete_note, note_id)

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
            graph = await asyncio.to_thread(NoteGraph, resolved)
            data = await asyncio.to_thread(graph.build_graph, include_code=include_code)

            # Short excerpts in graph nodes; full text via trellis_get_note
            for node in data.get("nodes", []):
                if isinstance(node.get("content"), str):
                    node["content"] = _truncate(node["content"], 300)

            return _dump(data)
        except Exception as e:
            return _dump({"error": str(e)})

    return mcp
