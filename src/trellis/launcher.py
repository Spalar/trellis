"""Launcher module for bundled Trellis releases.

When PyInstaller bundles the app, it runs server.py as the entry point.
This module ensures environment variables are set for a single-click launch.
"""

import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

TOKEN_FILE = "api-token"


def get_bundle_dir() -> Path:
    """Get the directory where the bundled app runs from."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent  # repo root (src/trellis/..)


def setup_environment() -> None:
    """Set required environment variables for bundled release."""
    bundle_dir = get_bundle_dir()

    # Ensure code-graph-mcp binary is found
    if os.environ.get("PATH"):
        os.environ["PATH"] = str(bundle_dir / "bin") + os.pathsep + os.environ["PATH"]
    else:
        os.environ["PATH"] = str(bundle_dir / "bin")

    # HTTP mode for visualizer + MCP over HTTP
    os.environ.setdefault("TRELLIS_TRANSPORT", "http")
    os.environ.setdefault("TRELLIS_HOST", "127.0.0.1")
    os.environ.setdefault("TRELLIS_PORT", "17317")
    os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
    os.environ.setdefault("FASTMCP_LOG_LEVEL", "ERROR")

    # Add src to path for imports when not frozen
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(bundle_dir / "src"))


def ensure_api_token(data_dir: Path = None) -> str:
    """Bundled HTTP mode: make sure clients need a token, and tell the user it.

    Uses TRELLIS_API_KEY when set. Otherwise reuses (or creates) a token in
    <data dir>/api-token, so MCP-over-HTTP clients keep working across
    restarts, and exports it as TRELLIS_API_KEY. The UI needs nothing: the
    page it serves carries the token. Messages go to stderr; stdout may be
    an MCP stdio channel.
    """
    if os.environ.get("TRELLIS_API_KEY", "").strip():
        return os.environ["TRELLIS_API_KEY"].strip()

    if data_dir is None:
        from src.trellis.utils import get_trellis_data_dir

        data_dir = get_trellis_data_dir()
    path = Path(data_dir) / TOKEN_FILE
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        token = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        try:
            os.chmod(path, 0o600)  # owner-only where the OS supports it
        except OSError:
            pass
    os.environ["TRELLIS_API_KEY"] = token
    print(
        f"Trellis API token (saved in {path}):\n  {token}\n"
        "HTTP MCP and API clients send: Authorization: Bearer <token>",
        file=sys.stderr,
        flush=True,
    )
    return token


def open_browser() -> None:
    """Open browser after short delay to let server start."""

    def _open():
        import time

        time.sleep(2)
        webbrowser.open("http://localhost:17317")

    thread = threading.Thread(target=_open, daemon=True)
    thread.start()
