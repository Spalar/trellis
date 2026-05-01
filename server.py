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
            "risk_level": report.get("technical_impact", {}).get("risk_level", "unknown"),
            "affected_functions": len(report.get("technical_impact", {}).get("affected_functions", [])),
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
        graph = bridge.get_graph_for_visualizer(max_nodes=200)
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
        
        uvicorn.run(http_app, host="0.0.0.0", port=17317)
