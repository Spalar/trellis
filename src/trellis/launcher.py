"""Launcher module for bundled Trellis releases.

When PyInstaller bundles the app, it runs server.py as the entry point.
This module ensures environment variables are set for a single-click launch.
"""

import os
import sys
import threading
import webbrowser
from pathlib import Path


def get_bundle_dir() -> Path:
    """Get the directory where the bundled app runs from."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


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
    os.environ.setdefault("TRELLIS_ALLOW_NO_AUTH", "true")
    os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
    os.environ.setdefault("FASTMCP_LOG_LEVEL", "ERROR")

    # Add src to path for imports when not frozen
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(bundle_dir / "src"))


def open_browser() -> None:
    """Open browser after short delay to let server start."""

    def _open():
        import time

        time.sleep(2)
        webbrowser.open("http://localhost:17317")

    thread = threading.Thread(target=_open, daemon=True)
    thread.start()
