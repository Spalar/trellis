"""Visualizer UI routes for Trellis.

`mount_ui_routes` adds the visualizer (``/``) and vendored static assets to
any FastAPI app — the API serves the UI by default, so every mode exposes it
on the same origin (disable with ``TRELLIS_UI=off``). ``create_ui_app``
builds the standalone UI+API app (optionally splicing in an MCP HTTP app),
sharing the same route mounting.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.responses import FileResponse, JSONResponse

from src.trellis import core


def mount_ui_routes(app: FastAPI, version: str) -> None:
    """Add the visualizer UI routes (``/`` and ``/vendor/{filename}``) to app.

    The served page embeds the launch token so same-origin fetches are
    authenticated. On loopback binds ``/`` is public (the guard's Host/Origin
    checks keep the token local-only); on trusted-network binds the guard
    requires credentials for ``/`` too, so the page only ever renders for
    callers who already hold the token.
    """
    visualizer_path = core.TRELLIS_ROOT / "visualizer.html"

    @app.get("/")
    async def root():
        """Serve visualizer HTML with the launch token embedded."""
        if visualizer_path.exists():
            html = visualizer_path.read_text(encoding="utf-8")
            token_script = (
                f'<script>window.TRELLIS_TOKEN = "{core.current_token()}";</script>'
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
    mount_ui_routes(app, version)

    # Added last-but-one so the UI routes above take precedence (Starlette
    # matches in registration order); the API mount is the catch-all. The MCP
    # routes come before it. The API app carries its own guard middleware.
    if mcp_app is not None:
        app.router.routes.extend(mcp_app.routes)
    app.mount("/", api_app)

    return app
