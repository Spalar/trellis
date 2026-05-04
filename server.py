"""Trellis MCP server - graph-based code analysis and impact detection.

This module exposes both HTTP routes (for the visualizer) and MCP tools
(for AI coding agents). Uses code-graph-mcp via CodeGraphBridge.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

import time
from typing import Dict, List, Optional

from analytics import AnalyticsStore
from auth import validate_auth
from spec_manager import SpecManager

# Load environment variables from .env file
load_dotenv(Path(__file__).with_name(".env"))

# Ensure banner is suppressed via env var
if os.environ.get("FASTMCP_SHOW_SERVER_BANNER") is None:
    os.environ["FASTMCP_SHOW_SERVER_BANNER"] = "false"
if os.environ.get("FASTMCP_LOG_LEVEL") is None:
    os.environ["FASTMCP_LOG_LEVEL"] = "ERROR"

VERSION = "0.2.0"

mcp = FastMCP(
    "trellis-core",
    version=VERSION,
    strict_input_validation=True,
    mask_error_details=True,
)

# ------------------------------------------------------------------
# State (initialized once at import time)
# ------------------------------------------------------------------
_spec_manager = SpecManager()
_analytics = AnalyticsStore()

# Cache bridge instances by project path
_bridge_cache: Dict[str, "CodeGraphBridge"] = {}


def _resolve_project_path(project_id: str) -> str:
    """Resolve project ID to actual path."""
    if project_id == "trellis":
        return str(Path(__file__).parent)
    
    path = Path(project_id)
    if path.is_absolute() and path.exists():
        return str(path)
    
    # Try relative to current directory
    if path.exists():
        return str(path.resolve())
    
    # Try relative to trellis root
    trellis_root = Path(__file__).parent
    candidate = trellis_root / project_id
    if candidate.exists():
        return str(candidate.resolve())
    
    return project_id  # Fallback


def _get_bridge(project_id: str) -> "CodeGraphBridge":
    """Get or create bridge for project."""
    if project_id not in _bridge_cache:
        from src.trellis import CodeGraphBridge
        resolved = _resolve_project_path(project_id)
        _bridge_cache[project_id] = CodeGraphBridge(resolved)
    return _bridge_cache[project_id]


def _track_tool(tool_name: str):
    """Decorator to track tool calls with timing."""
    def decorator(func):
        import functools
        @functools.wraps(func)
        async def wrapper(project_id: str = "", **kwargs):
            start = time.perf_counter()
            status = "success"
            error_msg = None
            
            try:
                result = await func(project_id=project_id, **kwargs)
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
        
        return wrapper
    return decorator


# ------------------------------------------------------------------
# MCP Tools
# ------------------------------------------------------------------

@mcp.tool()
@_track_tool("trellis_sync")
async def trellis_sync(
    project_id: str = "",
    repo_path: str = "",
    config_path: str = ".trellis/config.yaml",
    incremental: bool = False,
) -> str:
    """Sync a project repository into the graph.
    
    Uses code-graph-mcp to index the codebase.
    """
    try:
        bridge = _get_bridge(repo_path or project_id)
        health = bridge.health_check()
        return json.dumps({
            "status": "ok",
            "project_id": project_id,
            "nodes": health.get("nodes_count", 0),
            "files": health.get("files_count", 0),
            "message": "Project synced successfully",
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_get_feature")
async def trellis_get_feature(
    project_id: str = "",
    feature_name: str = "",
) -> str:
    """Get feature context and functions."""
    try:
        bridge = _get_bridge(project_id)
        module = bridge.module_overview(feature_name)
        return json.dumps(module, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_analyze_impact")
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
            "development_pointers": report.get("development_pointers", [])[:10],  # Limit
            "divergence_warnings": report.get("divergence_warnings", []),
        }
        
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_get_function")
async def trellis_get_function(
    project_id: str = "",
    function_path: str = "",
) -> str:
    """Get details for a specific function."""
    try:
        bridge = _get_bridge(project_id)
        node = bridge.get_ast_node(function_path)
        return json.dumps(node, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_search")
async def trellis_search(
    project_id: str = "",
    query: str = "",
    limit: int = 10,
) -> str:
    """Search for functions by concept."""
    try:
        bridge = _get_bridge(project_id)
        results = bridge.search(query, limit=limit)
        return json.dumps({"results": results}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_list_features")
async def trellis_list_features(
    project_id: str = "",
) -> str:
    """List all features/modules in the project."""
    try:
        bridge = _get_bridge(project_id)
        pmap = bridge.project_map()
        modules = pmap.get("modules", [])
        return json.dumps({
            "features": [
                {
                    "name": m.get("path", "unknown"),
                    "symbol_count": m.get("symbol_count", 0),
                }
                for m in modules
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_trace_path")
async def trellis_trace_path(
    project_id: str = "",
    from_feature: str = "",
    to_feature: str = "",
) -> str:
    """Trace dependency path between two features."""
    try:
        bridge = _get_bridge(project_id)
        deps = bridge.dependency_graph(from_feature)
        return json.dumps(deps, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_visualize_graph")
async def trellis_visualize_graph(
    project_id: str = "",
) -> str:
    """Get graph data for visualization."""
    try:
        bridge = _get_bridge(project_id)
        graph = bridge.get_graph_for_visualizer(max_nodes=200)
        return json.dumps(graph, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_detect_hotspots")
async def trellis_detect_hotspots(
    project_id: str = "",
) -> str:
    """Find high-centrality functions (potential hotspots)."""
    try:
        bridge = _get_bridge(project_id)
        pmap = bridge.project_map()
        hot = pmap.get("hot_functions", [])[:20]
        return json.dumps({"hotspots": hot}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_analyze_diff")
async def trellis_analyze_diff(
    project_id: str = "",
    diff: str = "",
) -> str:
    """Analyze impact of a code diff."""
    try:
        # Parse diff to find changed functions
        # This is a simplified version
        return json.dumps({
            "message": "Diff analysis requires git integration. Use trellis_analyze_impact for specific functions.",
            "diff_length": len(diff),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_get_boundary_map")
async def trellis_get_boundary_map(
    project_id: str = "",
) -> str:
    """Get module boundary map."""
    try:
        bridge = _get_bridge(project_id)
        pmap = bridge.project_map()
        modules = pmap.get("modules", [])
        deps = pmap.get("module_dependencies", [])
        return json.dumps({
            "modules": [m.get("path") for m in modules],
            "dependencies": deps,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


# ------------------------------------------------------------------
# Knowledge Graph Tools
# ------------------------------------------------------------------

@mcp.tool()
@_track_tool("trellis_create_note")
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
        
        return json.dumps({
            "status": "ok",
            "note_id": note.id,
            "title": note.title,
            "links": note.links,
            "mentions": note.mentions,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_get_note")
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
            return json.dumps({"error": f"Note '{note_id}' not found"}, indent=2)
        
        return json.dumps({
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "tags": note.tags,
            "links": note.links,
            "mentions": note.mentions,
            "backlinks": graph.get_backlinks(note_id),
            "created": note.created_at,
            "updated": note.updated_at,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_search_notes")
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
        
        return json.dumps({
            "query": query,
            "results": [
                {
                    "id": n.id,
                    "title": n.title,
                    "excerpt": n.content[:200] + "..." if len(n.content) > 200 else n.content,
                    "tags": n.tags,
                }
                for n in results
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_delete_note")
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
        
        return json.dumps({
            "status": "ok" if success else "error",
            "message": f"Note '{note_id}' deleted" if success else f"Note '{note_id}' not found",
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_knowledge_graph")
async def trellis_knowledge_graph(
    project_id: str = "",
) -> str:
    """Get the full knowledge graph (notes + code nodes + edges)."""
    try:
        from src.trellis.knowledge_graph import NoteGraph
        
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        data = graph.build_graph(include_code=True)
        
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


# ------------------------------------------------------------------
# HTTP Routes
# ------------------------------------------------------------------

@mcp.custom_route("/", methods=["GET"])
async def root(request: Request):
    """Serve visualizer HTML."""
    visualizer_path = Path(__file__).parent / "visualizer.html"
    if visualizer_path.exists():
        return FileResponse(visualizer_path)
    return JSONResponse({"message": "Trellis MCP Server", "version": VERSION})


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    """Health check."""
    return JSONResponse({"status": "ok", "version": VERSION})


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
        if spec is None:
            template = _spec_manager.create_template(project_id)
            return JSONResponse({
                "project_id": project_id,
                "status": "no_spec",
                "content": template,
            })
        return JSONResponse({
            "project_id": project_id,
            "status": "ok",
            "content": spec.content,
        })
    
    else:  # POST
        body = await request.json()
        content = body.get("content", "")
        _spec_manager.save_spec(project_id, content)
        return JSONResponse({"project_id": project_id, "status": "ok"})


@mcp.custom_route("/analytics", methods=["GET"])
async def analytics_dashboard(request: Request):
    """Serve analytics dashboard."""
    dashboard_path = Path(__file__).parent / "analytics.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return JSONResponse({"error": "Analytics dashboard not found"}, status_code=404)


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
                return JSONResponse({"error": f"Note '{note_id}' not found"}, status_code=404)
            return JSONResponse({
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "tags": note.tags,
                "links": note.links,
                "mentions": note.mentions,
                "backlinks": graph.get_backlinks(note_id),
            })
        elif request.method == "POST":
            body = await request.json()
            content = body.get("content", "")
            title = body.get("title", note_id)
            tags = body.get("tags", [])
            note = graph.save_note(note_id, content, title=title, tags=tags)
            return JSONResponse({"status": "ok", "note_id": note.id, "title": note.title})
        elif request.method == "DELETE":
            success = graph.delete_note(note_id)
            if not success:
                return JSONResponse({"error": f"Note '{note_id}' not found"}, status_code=404)
            return JSONResponse({"status": "ok", "message": f"Note '{note_id}' deleted"})
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


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Determine transport from environment
    transport = os.environ.get("TRELLIS_TRANSPORT", "stdio")
    
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # HTTP mode - Use FastAPI for REST endpoints
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse
        import uvicorn
        
        http_app = FastAPI(title="Trellis API", version=VERSION)
        
        # Enable CORS
        http_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        @http_app.get("/")
        async def root():
            """Serve visualizer HTML."""
            visualizer_path = Path(__file__).parent / "visualizer.html"
            if visualizer_path.exists():
                return FileResponse(visualizer_path)
            return {"message": "Trellis MCP Server", "version": VERSION}
        
        @http_app.get("/health")
        async def health():
            return {"status": "ok", "version": VERSION}
        
        @http_app.get("/graph/{project_id}")
        async def graph_get(project_id: str):
            try:
                bridge = _get_bridge(project_id)
                graph = bridge.get_graph_for_visualizer(max_nodes=200)
                return graph
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/graph/{project_id}/impact/{symbol}")
        async def graph_impact(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                graph = bridge.get_impact_graph(symbol)
                return graph
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/feature/{project_id}/impact/{symbol}")
        async def feature_impact(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                report = bridge.get_feature_impact(symbol)
                return report
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/feature/{project_id}/context/{symbol}")
        async def feature_context(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                context = bridge.get_feature_context(symbol)
                if context:
                    return context
                raise HTTPException(status_code=404, detail="No feature context found")
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/feature/{project_id}/pointers/{symbol}")
        async def feature_pointers(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                pointers = bridge.get_development_pointers(symbol)
                return {"pointers": pointers}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/feature/{project_id}/divergence/{symbol}")
        async def feature_divergence(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                warnings = bridge.check_feature_divergence(symbol)
                return {"symbol": symbol, "divergence_warnings": warnings, "has_divergence": len(warnings) > 0}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/spec/{project_id}")
        async def spec_get(project_id: str):
            spec = _spec_manager.load_spec(project_id)
            if spec is None:
                template = _spec_manager.create_template(project_id)
                return {"project_id": project_id, "status": "no_spec", "content": template}
            return {"project_id": project_id, "status": "ok", "content": spec.content}
        
        @http_app.post("/spec/{project_id}")
        async def spec_post(project_id: str, content: str = ""):
            _spec_manager.save_spec(project_id, content)
            return {"project_id": project_id, "status": "ok"}
        
        @http_app.get("/analytics")
        async def analytics_dashboard():
            dashboard_path = Path(__file__).parent / "analytics.html"
            if dashboard_path.exists():
                return FileResponse(dashboard_path)
            raise HTTPException(status_code=404, detail="Dashboard not found")
        
        @http_app.get("/knowledge-graph/{project_id}")
        async def knowledge_graph_get(project_id: str):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                data = graph.build_graph(include_code=True)
                return data
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/note/{project_id}/{note_id}")
        async def note_get(project_id: str, note_id: str):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                note = graph.get_note(note_id)
                if not note:
                    raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found")
                return {
                    "id": note.id,
                    "title": note.title,
                    "content": note.content,
                    "tags": note.tags,
                    "links": note.links,
                    "mentions": note.mentions,
                    "backlinks": graph.get_backlinks(note_id),
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.post("/note/{project_id}/{note_id}")
        async def note_post(project_id: str, note_id: str, body: dict):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                content = body.get("content", "")
                title = body.get("title", note_id)
                tags = body.get("tags", [])
                note = graph.save_note(note_id, content, title=title, tags=tags)
                return {"status": "ok", "note_id": note.id, "title": note.title}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.delete("/note/{project_id}/{note_id}")
        async def note_delete(project_id: str, note_id: str):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                success = graph.delete_note(note_id)
                if not success:
                    raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found")
                return {"status": "ok", "message": f"Note '{note_id}' deleted"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/notes/{project_id}")
        async def notes_list(project_id: str):
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
                return {"notes": notes, "count": len(notes)}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/projects")
        async def list_projects():
            """List available projects with graph data."""
            import os
            projects = []
            
            # Current directory (trellis itself)
            current = Path(__file__).parent
            if (current / ".code-graph").exists() or (current / ".trellis").exists():
                projects.append({
                    "id": "trellis",
                    "name": "Trellis",
                    "path": str(current),
                })
            
            # Parent directory - scan for subdirectories with .code-graph or .trellis
            parent = current.parent
            if parent.exists():
                for item in parent.iterdir():
                    if item.is_dir() and item.name != current.name:
                        has_graph = (item / ".code-graph").exists() or (item / ".trellis").exists()
                        # Also check for common project indicators
                        is_project = has_graph or (item / ".git").exists() or (item / "src").exists()
                        if is_project:
                            projects.append({
                                "id": item.name,
                                "name": item.name,
                                "path": str(item),
                            })
            
            return {"projects": projects}
        
        uvicorn.run(http_app, host="0.0.0.0", port=17317)
