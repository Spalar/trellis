"""Trellis MCP server - graph-based code analysis and impact detection.

This module exposes both HTTP routes (for the visualizer) and MCP tools
(for AI coding agents). All state is initialized at module level for
simplicity since FastMCP handles the server lifecycle.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse

import time
from typing import List

from analytics import AnalyticsStore
from auth import validate_auth
from engine import TrellisEngine
from extractor import PythonTreeSitterExtractor
from models import (
    BoundaryMap,
    DiffImpactReport,
    FeatureContext,
    FeatureList,
    FunctionDetail,
    HotspotReport,
    ImpactReport,
    PathTrace,
    SearchResult,
    SyncResult,
)
from router import FeatureRouter
from spec_manager import SpecManager
from store import GraphStore
from visualizer import GraphVisualizer

# Load environment variables from .env file
load_dotenv(Path(__file__).with_name(".env"))

VERSION = "0.1.0"

mcp = FastMCP(
    "trellis-core",
    version=VERSION,
    strict_input_validation=True,
    mask_error_details=True,
)

# ------------------------------------------------------------------
# State (initialized once at import time)
# ------------------------------------------------------------------
_store = GraphStore()
_engine = TrellisEngine(store=_store, extractor=PythonTreeSitterExtractor())
_router = FeatureRouter(store=_store)
_visualizer = GraphVisualizer(store=_store)
_spec_manager = SpecManager()
_analytics = AnalyticsStore()


def _track_tool(tool_name: str):
    """Decorator to track tool calls with timing and store metrics."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            project_id = kwargs.get("project_id", "unknown")
            status = "success"
            error_msg = None
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                error_msg = str(e)
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                _analytics.record_tool_call(
                    tool_name=tool_name,
                    duration_ms=duration_ms,
                    status=status,
                    project_id=project_id,
                    error_message=error_msg,
                )
        
        # Preserve original function metadata for FastMCP
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        wrapper.__annotations__ = func.__annotations__
        return wrapper
    return decorator


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _cors_json(data: dict, status: int = 200) -> JSONResponse:
    """Wrap data in a JSONResponse with CORS headers for the visualizer."""
    resp = JSONResponse(data, status_code=status)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


def _auth(request: Request) -> None:
    """Validate auth from an HTTP request."""
    validate_auth(dict(request.headers))


def _project_id(request: Request) -> str:
    """Extract and validate project_id from path params."""
    pid = request.path_params.get("project_id", "").strip()
    if not pid:
        raise ValueError("project_id is required")
    return pid


def _path_param(request: Request, name: str) -> str:
    """Extract a path param and strip whitespace."""
    return request.path_params.get(name, "").strip()


def _auth_from_context(ctx) -> None:
    """Validate auth from an MCP tool context."""
    headers = {}
    request = getattr(ctx, "request", None)
    if request is not None:
        headers = getattr(request, "headers", {}) or {}
    validate_auth(headers)


def _require_project_id(project_id: str) -> str:
    normalized = project_id.strip()
    if not normalized:
        raise ToolError("project_id is required")
    return normalized


def _validate_repo_path(repo_path: str) -> str:
    path = Path(repo_path).resolve()
    if not path.exists() or not path.is_dir():
        raise ToolError("repo_path must be an existing directory")
    return path.as_posix()


# ------------------------------------------------------------------
# HTTP Routes (for visualizer)
# ------------------------------------------------------------------
@mcp.custom_route("/health", methods=["GET"])
async def health_check(_: Request) -> JSONResponse:
    return _cors_json(
        {"status": "ok", "projects_cached": _router.project_count(), "version": VERSION}
    )


@mcp.custom_route("/graph/{project_id}", methods=["GET"])
async def visualizer_graph(request: Request) -> JSONResponse:
    """Export full project graph as D3.js nodes/links JSON."""
    _auth(request)
    try:
        pid = _project_id(request)
        data = _visualizer.export_graph(pid)
        return _cors_json(data)
    except ValueError as exc:
        return _cors_json({"error": str(exc)}, status=400)
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


@mcp.custom_route("/graph/{project_id}/nodes", methods=["GET"])
async def visualizer_graph_nodes(request: Request) -> JSONResponse:
    """Export paginated project graph nodes for lazy loading."""
    _auth(request)
    try:
        pid = _project_id(request)
    except ValueError as exc:
        return _cors_json({"error": str(exc)}, status=400)

    layer = request.query_params.get("layer", "feature")
    limit = int(request.query_params.get("limit", "50"))
    offset = int(request.query_params.get("offset", "0"))

    try:
        data = _visualizer.export_graph(pid)
        all_nodes = data.get("nodes", [])
        filtered = [n for n in all_nodes if layer == "all" or n.get("type") == layer]
        paginated = filtered[offset : offset + limit]
        node_ids = {n["id"] for n in paginated}
        links = [
            link
            for link in data.get("links", [])
            if link.get("source") in node_ids or link.get("target") in node_ids
        ]
        return _cors_json(
            {
                "nodes": paginated,
                "links": links,
                "total": len(filtered),
                "offset": offset,
                "limit": limit,
                "has_more": (offset + limit) < len(filtered),
            }
        )
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


@mcp.custom_route("/graph/{project_id}/impact/{function_path:path}", methods=["GET"])
async def visualizer_impact(request: Request) -> JSONResponse:
    """Export impact subgraph for a function path."""
    _auth(request)
    pid = _path_param(request, "project_id")
    func_path = _path_param(request, "function_path")
    if not pid:
        return _cors_json({"error": "project_id is required"}, status=400)
    if not func_path:
        return _cors_json({"error": "function_path is required"}, status=400)
    try:
        # Strip visualizer prefix if present
        clean_path = func_path.replace("func:", "", 1) if func_path.startswith("func:") else func_path
        
        # Resolve function path first (handles fuzzy matching)
        resolved = _engine._resolve_function_path(pid, clean_path)
        if resolved is None:
            return _cors_json({"error": f"Function not found: {func_path}"}, status=404)
        
        # Get impact report for risk annotations
        try:
            impact_report = _engine.analyze_impact(
                project_id=pid,
                function_path=resolved,
                change_summary="",
                include_suggestions=True,
                depth_mode="standard",
            )
            data = _visualizer.export_impact_subgraph(pid, resolved, impact_report.model_dump())
        except Exception:
            # Fallback to basic subgraph if impact analysis fails
            data = _visualizer.export_impact_subgraph(pid, resolved)
        return _cors_json(data)
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


@mcp.custom_route("/graph/{project_id}/impact-details/{function_path:path}", methods=["GET"])
async def visualizer_impact_details(request: Request) -> JSONResponse:
    """Export enhanced impact analysis with risk groups, breakpoints, and test suggestions."""
    _auth(request)
    pid = _path_param(request, "project_id")
    func_path = _path_param(request, "function_path")
    if not pid:
        return _cors_json({"error": "project_id is required"}, status=400)
    if not func_path:
        return _cors_json({"error": "function_path is required"}, status=400)
    try:
        report = _engine.analyze_impact(
            project_id=pid,
            function_path=func_path,
            change_summary="",
            include_suggestions=True,
            depth_mode="standard",
        )
        return _cors_json(report.model_dump())
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


@mcp.custom_route("/graph/{project_id}/hotspots", methods=["GET"])
async def visualizer_hotspots(request: Request) -> JSONResponse:
    """Export architectural hotspots for visualization."""
    _auth(request)
    try:
        pid = _project_id(request)
    except ValueError as exc:
        return _cors_json({"error": str(exc)}, status=400)
    try:
        report = _engine.detect_hotspots(pid)
        return _cors_json(report.model_dump())
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


@mcp.custom_route("/visualizer", methods=["GET"])
async def visualizer_page(_: Request) -> FileResponse:
    """Serve the standalone HTML graph visualizer."""
    path = Path(__file__).with_name("visualizer.html")
    if not path.exists():
        return _cors_json({"error": "visualizer.html not found"}, status=404)
    return FileResponse(path, media_type="text/html")


@mcp.custom_route("/", methods=["GET"])
async def root_redirect(_: Request) -> RedirectResponse:
    """Redirect root to the visualizer."""
    return RedirectResponse(url="/visualizer")


@mcp.custom_route("/analytics", methods=["GET"])
async def analytics_dashboard(_: Request) -> FileResponse:
    """Serve the analytics dashboard HTML."""
    path = Path(__file__).with_name("analytics.html")
    if not path.exists():
        return _cors_json({"error": "analytics.html not found"}, status=404)
    return FileResponse(path, media_type="text/html")


@mcp.custom_route("/analytics/api/stats", methods=["GET"])
async def analytics_stats(_: Request) -> JSONResponse:
    """Return analytics statistics."""
    try:
        stats = _analytics.get_summary_stats()
        return _cors_json(stats)
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


@mcp.custom_route("/analytics/api/calls", methods=["GET"])
async def analytics_calls(request: Request) -> JSONResponse:
    """Return recent tool calls."""
    try:
        hours = int(request.query_params.get("hours", "24"))
        limit = int(request.query_params.get("limit", "50"))
        calls = _analytics.get_recent_calls(limit=limit)
        stats = _analytics.get_tool_call_stats(hours=hours)
        return _cors_json({"calls": calls, "stats": stats})
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


@mcp.custom_route("/analytics/api/syncs", methods=["GET"])
async def analytics_syncs(request: Request) -> JSONResponse:
    """Return sync history."""
    try:
        project_id = request.query_params.get("project_id")
        limit = int(request.query_params.get("limit", "20"))
        syncs = _analytics.get_sync_history(project_id=project_id, limit=limit)
        return _cors_json(syncs)
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


@mcp.custom_route("/spec/{project_id}", methods=["GET"])
async def spec_get(request: Request) -> JSONResponse:
    """Return project.md spec for a project."""
    _auth(request)
    try:
        pid = _project_id(request)
    except ValueError as exc:
        return _cors_json({"error": str(exc)}, status=400)

    spec = _spec_manager.load_spec(pid)
    if spec is None:
        template = _spec_manager.create_template(pid)
        return _cors_json(
            {
                "project_id": pid,
                "status": "no_spec",
                "content": template,
                "hint": "No project.md found. Use the template above or create your own.",
            }
        )

    return _cors_json(
        {
            "project_id": pid,
            "status": "ok",
            "content": spec.content,
            "source_path": spec.source_path,
            "overview": spec.overview,
            "purpose": spec.purpose,
            "architecture": spec.architecture,
        }
    )


@mcp.custom_route("/spec/{project_id}", methods=["POST"])
async def spec_save(request: Request) -> JSONResponse:
    """Save project.md spec for a project."""
    _auth(request)
    try:
        pid = _project_id(request)
    except ValueError as exc:
        return _cors_json({"error": str(exc)}, status=400)

    try:
        body = await request.json() if await request.body() else {}
    except Exception:
        body = {}

    content = body.get("content", "")
    if not content:
        return _cors_json({"error": "content is required"}, status=400)

    try:
        saved_path = _spec_manager.save_spec(pid, content)
        return _cors_json(
            {
                "project_id": pid,
                "status": "ok",
                "saved_to": str(saved_path),
                "message": "Project spec saved.",
            }
        )
    except Exception as exc:
        return _cors_json({"error": str(exc)}, status=500)


# ------------------------------------------------------------------
# MCP Tools (for AI coding agents)
# ------------------------------------------------------------------
@mcp.tool()
async def trellis_sync(
    project_id: str,
    repo_path: str,
    config_path: str = ".trellis/config.yaml",
    incremental: bool = True,
    ctx=None,
) -> SyncResult:
    """Sync a repository into a project-scoped Trellis graph snapshot."""
    import time
    start = time.perf_counter()
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    rpath = _validate_repo_path(repo_path)
    result = _engine.sync_project(
        project_id=pid,
        repo_path=rpath,
        config_path=config_path,
        incremental=incremental,
    )
    _router.refresh(pid)
    duration_ms = (time.perf_counter() - start) * 1000
    _analytics.record_sync(
        project_id=pid,
        duration_ms=duration_ms,
        functions_indexed=result.indexed_functions,
        features_indexed=result.indexed_features,
        incremental=incremental,
    )
    _analytics.record_tool_call(
        tool_name="trellis_sync",
        duration_ms=duration_ms,
        status="success",
        project_id=pid,
    )
    return result


@_track_tool("trellis_get_feature")
@mcp.tool()
async def trellis_get_feature(
    project_id: str,
    feature_name: str,
    include_dependencies: bool = False,
    depth: int = 1,
    ctx=None,
) -> FeatureContext:
    """Return feature context and optional dependency neighborhood."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    if not (1 <= depth <= 10):
        raise ToolError("depth must be between 1 and 10")
    try:
        return _engine.get_feature(pid, feature_name, include_dependencies, depth)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@_track_tool("trellis_analyze_impact")
@mcp.tool()
async def trellis_analyze_impact(
    project_id: str,
    function_path: str,
    change_summary: str = "",
    include_suggestions: bool = True,
    depth_mode: str = "standard",
    ctx=None,
) -> ImpactReport:
    """Analyze impacted functions/features for a proposed function change.

    depth_mode: "standard" (call graph), "deep" (call + data flow), "full" (all dimensions)
    """
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    if depth_mode not in {"standard", "deep", "full"}:
        raise ToolError('depth_mode must be one of: standard, deep, full')
    try:
        return _engine.analyze_impact(
            project_id=pid,
            function_path=function_path,
            change_summary=change_summary,
            include_suggestions=include_suggestions,
            depth_mode=depth_mode,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@_track_tool("trellis_trace_path")
@mcp.tool()
async def trellis_trace_path(
    project_id: str,
    from_feature: str,
    to_feature: str,
    max_depth: int = 5,
    ctx=None,
) -> PathTrace:
    """Trace a dependency path between two features within a project graph."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    if not (1 <= max_depth <= 20):
        raise ToolError("max_depth must be between 1 and 20")
    return _engine.trace_path(pid, from_feature, to_feature, max_depth)


@_track_tool("trellis_search")
@mcp.tool()
async def trellis_search(
    project_id: str,
    query: str,
    search_type: str = "auto",
    limit: int = 5,
    ctx=None,
) -> SearchResult:
    """Search project graph metadata by semantic or keyword strategy."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    normalized = search_type.lower().strip()
    if normalized not in {"auto", "semantic", "keyword"}:
        raise ToolError("search_type must be one of: auto, semantic, keyword")
    if not (1 <= limit <= 50):
        raise ToolError("limit must be between 1 and 50")
    return _engine.search(pid, query, normalized, limit)


@_track_tool("trellis_list_features")
@mcp.tool()
async def trellis_list_features(
    project_id: str,
    include_stats: bool = False,
    ctx=None,
) -> FeatureList:
    """List discovered features for a project with optional stats."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    return _engine.list_features(pid, include_stats)


@_track_tool("trellis_get_function")
@mcp.tool()
async def trellis_get_function(
    project_id: str,
    function_path: str,
    include_callers: bool = True,
    include_callees: bool = True,
    ctx=None,
) -> FunctionDetail:
    """Return function detail with optional caller/callee context."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    try:
        return _engine.get_function(pid, function_path, include_callers, include_callees)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@_track_tool("trellis_visualize_graph")
@mcp.tool()
async def trellis_visualize_graph(
    project_id: str,
    ctx=None,
) -> str:
    """Return a URL to open the interactive graph visualizer for this project."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    index = _store.load_index(pid)
    if index is None:
        raise ToolError(f"Project not found: {pid}")
    host = os.getenv("TRELLIS_HOST", "localhost").strip()
    port = os.getenv("TRELLIS_PORT", "17317").strip()
    return f"http://{host}:{port}/visualizer?project_id={pid}"


@_track_tool("trellis_analyze_feature_impact")
@mcp.tool()
async def trellis_analyze_feature_impact(
    project_id: str,
    feature_name: str,
    change_summary: str = "",
    include_suggestions: bool = True,
    ctx=None,
) -> ImpactReport:
    """Analyze impacted features/functions for a proposed feature-level change."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    try:
        return _engine.analyze_feature_impact(
            project_id=pid,
            feature_name=feature_name,
            change_summary=change_summary,
            include_suggestions=include_suggestions,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@_track_tool("trellis_detect_hotspots")
@mcp.tool()
async def trellis_detect_hotspots(
    project_id: str,
    ctx=None,
) -> HotspotReport:
    """Detect architectural hotspots: high-centrality nodes and unstable dependency clusters."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    return _engine.detect_hotspots(pid)


@_track_tool("trellis_analyze_diff")
@mcp.tool()
async def trellis_analyze_diff(
    project_id: str,
    repo_path: str,
    diff_source: str = "git",
    ctx=None,
) -> DiffImpactReport:
    """Read current branch diff and re-rank impact in near real-time."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    rpath = _validate_repo_path(repo_path)
    return _engine.analyze_diff(pid, rpath, diff_source)


@_track_tool("trellis_get_boundary_map")
@mcp.tool()
async def trellis_get_boundary_map(
    project_id: str,
    ctx=None,
) -> List[BoundaryMap]:
    """Map impacts to module boundaries and owners. Detect cross-boundary violations."""
    _auth_from_context(ctx)
    pid = _require_project_id(project_id)
    return _engine.get_boundary_map(pid)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("TRELLIS_TRANSPORT", "stdio").strip().lower()
    host = os.getenv("TRELLIS_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("TRELLIS_PORT", "17317").strip() or "17317")

    try:
        if transport == "stdio":
            mcp.run(show_banner=False)
        elif transport in {"http", "sse"}:
            mcp.run(transport=transport, host=host, port=port, show_banner=False)
        else:
            raise RuntimeError("TRELLIS_TRANSPORT must be one of: stdio, http, sse")
    except KeyboardInterrupt:
        pass
