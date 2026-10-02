"""Visualizer UI app for Trellis.

Serves visualizer.html (with the launch token injected) and the vendored
static assets, and mounts the FastAPI backend (src/trellis/api.py) at "/"
after those routes so every API endpoint is reachable on the same origin.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.responses import FileResponse, JSONResponse

from src.trellis import core


def create_ui_app(api_app: FastAPI, version: str, mcp_app=None) -> FastAPI:
    """Build the UI app serving the visualizer plus the mounted API backend.

    When ``mcp_app`` is given (a FastMCP HTTP app, e.g. ``mcp.http_app()``),
    its MCP protocol route (at /mcp) is added so the endpoint shares the same
    port as the UI and REST routes — the layout the monolithic server used to
    serve. Its lifespan is wired into this app (required by FastMCP's
    streamable-http session manager). Note the routes are spliced in rather
    than mounted: a Mount("/mcp") does not match the bare "/mcp" path under
    Starlette 1.x.
    """
    lifespan = mcp_app.lifespan if mcp_app is not None else None
    app = FastAPI(title="trellis-ui", version=version, lifespan=lifespan)

    @app.get("/")
    async def root():
        """Serve visualizer HTML with the launch token embedded."""
        visualizer_path = core.TRELLIS_ROOT / "visualizer.html"
        if visualizer_path.exists():
            html = visualizer_path.read_text(encoding="utf-8")
            token_script = (
                f'<script>window.TRELLIS_TOKEN = "{core.http_token}";</script>'
            )
            if "</head>" in html:
                html = html.replace("</head>", token_script + "</head>", 1)
            else:
                html = token_script + html
            return HTMLResponse(html)
        return JSONResponse({"message": "Trellis UI", "version": version})

    @app.get("/vendor/{filename}")
    async def vendor_assets(filename: str):
        """Serve vendored JS libraries (d3, marked) — local, no CDN."""
        if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
            return JSONResponse({"error": "Invalid filename"}, status_code=400)
        asset = core.TRELLIS_ROOT / "vendor" / filename
        if not asset.is_file():
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(asset)

    # Added last-but-one so the routes above take precedence (Starlette matches
    # in registration order); the API mount is the catch-all. The MCP routes
    # come before it. The API app carries its own guard middleware.
    if mcp_app is not None:
        app.router.routes.extend(mcp_app.routes)
    app.mount("/", api_app)

    return app
