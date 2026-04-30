"""FastAPI server for Trellis visualizer.

Serves graph data from code-graph-mcp bridge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .bridge import CodeGraphBridge


app = FastAPI(title="Trellis Visualizer API")

# Enable CORS for visualizer
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache bridge instances by project path
_bridge_cache: Dict[str, CodeGraphBridge] = {}


def get_bridge(project_path: str) -> CodeGraphBridge:
    """Get or create bridge for project."""
    if project_path not in _bridge_cache:
        _bridge_cache[project_path] = CodeGraphBridge(project_path)
    return _bridge_cache[project_path]


@app.get("/")
async def root():
    """Serve the visualizer HTML."""
    visualizer_path = Path(__file__).parent.parent.parent / "visualizer.html"
    if visualizer_path.exists():
        return FileResponse(visualizer_path)
    return {"message": "Trellis Visualizer API"}


@app.get("/graph/{project_id}")
async def get_graph(project_id: str):
    """Get full graph for visualizer."""
    try:
        # Resolve project path
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        graph = bridge.get_graph_for_visualizer(max_nodes=200)
        return JSONResponse(content=graph)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph/{project_id}/impact/{symbol}")
async def get_impact_graph(project_id: str, symbol: str):
    """Get impact analysis graph."""
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        graph = bridge.get_impact_graph(symbol)
        return JSONResponse(content=graph)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph/{project_id}/search")
async def search_nodes(project_id: str, q: str, limit: int = 20):
    """Search for nodes."""
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        results = bridge.search(q, limit=limit)
        return JSONResponse(content={"results": results})
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph/{project_id}/node/{symbol}")
async def get_node_details(project_id: str, symbol: str):
    """Get details for a specific node."""
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        node = bridge.get_ast_node(symbol)
        return JSONResponse(content=node)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph/{project_id}/module/{module_path:path}")
async def get_module(project_id: str, module_path: str):
    """Get module overview."""
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        module = bridge.module_overview(module_path)
        return JSONResponse(content=module)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/feature/{project_id}/impact/{symbol}")
async def get_feature_impact(project_id: str, symbol: str, depth: int = 2):
    """Get feature-level impact analysis.
    
    Returns technical impact + feature context + development pointers.
    """
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        report = bridge.get_feature_impact(symbol, depth=depth)
        return JSONResponse(content=report)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/feature/{project_id}/context/{symbol}")
async def get_feature_context(project_id: str, symbol: str):
    """Get feature context for a function."""
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        context = bridge.get_feature_context(symbol)
        if context:
            return JSONResponse(content=context)
        else:
            raise HTTPException(status_code=404, detail="No feature context found")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/feature/{project_id}/pointers/{symbol}")
async def get_development_pointers(project_id: str, symbol: str):
    """Get development pointers for a function."""
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        pointers = bridge.get_development_pointers(symbol)
        return JSONResponse(content={"pointers": pointers})
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/feature/{project_id}/divergence/{symbol}")
async def check_divergence(project_id: str, symbol: str):
    """Check if function diverges from feature spec."""
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        warnings = bridge.check_feature_divergence(symbol)
        return JSONResponse(content={
            "symbol": symbol,
            "divergence_warnings": warnings,
            "has_divergence": len(warnings) > 0
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health/{project_id}")
async def health_check(project_id: str):
    """Get index health status."""
    try:
        project_path = _resolve_project_path(project_id)
        bridge = get_bridge(str(project_path))
        
        health = bridge.health_check()
        return JSONResponse(content=health)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _resolve_project_path(project_id: str) -> Path:
    """Resolve project ID to path.
    
    Supports:
    - Absolute paths
    - Relative paths from current directory
    - Special IDs like 'trellis' for the trellis repo itself
    """
    if project_id == "trellis":
        return Path(__file__).parent.parent.parent
    
    path = Path(project_id)
    if path.is_absolute():
        return path
    
    # Try relative to current directory
    if path.exists():
        return path.resolve()
    
    # Try relative to trellis root
    trellis_root = Path(__file__).parent.parent.parent
    candidate = trellis_root / project_id
    if candidate.exists():
        return candidate.resolve()
    
    raise ValueError(f"Project not found: {project_id}")


# For direct execution
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=17318)
