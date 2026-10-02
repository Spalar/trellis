"""Trellis entry point — dispatches to the MCP, API, UI, or sync components.

Usage:
    python server.py [mcp]  MCP server (stdio default; TRELLIS_TRANSPORT=http
                            serves MCP + REST + UI over HTTP on TRELLIS_HOST:
                            TRELLIS_PORT, default 127.0.0.1:17317)
    python server.py api    REST API only (FastAPI, /docs) on TRELLIS_HOST:
                            TRELLIS_PORT
    python server.py ui     Visualizer UI + API on TRELLIS_HOST:TRELLIS_PORT
    python server.py sync <repo> [--project-id <id>] [--incremental]
                            Headless index of a repo, then exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file in development only.
# In the bundled release, environment defaults are set by src/trellis/launcher.py.
if not getattr(sys, "frozen", False):
    load_dotenv(Path(__file__).with_name(".env"))

# Ensure banner is suppressed via env var
if os.environ.get("FASTMCP_SHOW_SERVER_BANNER") is None:
    os.environ["FASTMCP_SHOW_SERVER_BANNER"] = "false"
if os.environ.get("FASTMCP_LOG_LEVEL") is None:
    os.environ["FASTMCP_LOG_LEVEL"] = "ERROR"

VERSION = "0.3.0"

from src.trellis import core  # noqa: E402
from src.trellis.api import create_api_app  # noqa: E402
from src.trellis.mcp_server import create_mcp_server  # noqa: E402
from src.trellis.ui import create_ui_app  # noqa: E402

# Module-level MCP server, as before the split (external MCP configs run
# `python server.py` and rely on import side effects being cheap).
mcp = create_mcp_server(VERSION)

# Compatibility re-exports for callers/tests that import server directly.
_resolve_project_path = core.resolve_project_path
_get_bridge = core.get_bridge


def _require_key_for_network_bind(host: str | None = None) -> None:
    """Refuse to serve on a network interface without an API key.

    A non-loopback bind (0.0.0.0/:: or any non-loopback IP) exposes Trellis
    to the whole network, where the bearer token is the only defense — so
    TRELLIS_API_KEY must be set. Bind 127.0.0.1 instead for local-only use.
    """
    bind = host if host is not None else os.environ.get("TRELLIS_HOST", "127.0.0.1")
    if not core.is_non_loopback_host(bind):
        return
    if os.environ.get("TRELLIS_API_KEY", "").strip():
        return
    print(
        f"error: TRELLIS_HOST '{bind}' exposes Trellis on the network, but "
        "TRELLIS_API_KEY is not set. Set TRELLIS_API_KEY so clients must "
        "authenticate, or bind 127.0.0.1 for local-only access.",
        file=sys.stderr,
    )
    sys.exit(2)


def _run_mcp() -> None:
    """Run the FastMCP server: stdio by default, HTTP via TRELLIS_TRANSPORT.

    In the bundled (PyInstaller) release the launcher presets TRELLIS_TRANSPORT
    to http and opens a browser, so double-clicking trellis.exe serves the
    visualizer, REST endpoints, and the MCP endpoint on one port.
    """
    # Launcher and browser auto-open are only for bundled releases. In
    # development, or when used as an MCP server by OpenCode/Claude/etc.,
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

    transport = os.environ.get("TRELLIS_TRANSPORT", "stdio")

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # HTTP mode - serve the visualizer, REST endpoints, and MCP over HTTP
        # on one port, exactly as the pre-split monolith did. The MCP protocol
        # endpoint stays at /mcp so HTTP MCP clients need no URL change.
        import uvicorn

        app = create_ui_app(create_api_app(VERSION), VERSION, mcp_app=mcp.http_app())
        _require_key_for_network_bind()
        uvicorn.run(
            core.LocalHttpGuard(app),
            host=os.environ.get("TRELLIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("TRELLIS_PORT", "17317")),
        )


def _run_api() -> None:
    """Run the REST API only (no visualizer) on TRELLIS_HOST:TRELLIS_PORT."""
    if getattr(sys, "frozen", False):
        try:
            from src.trellis.launcher import setup_environment

            setup_environment()
        except ImportError:
            pass

    import uvicorn

    app = create_api_app(VERSION)
    _require_key_for_network_bind()
    uvicorn.run(
        app,
        host=os.environ.get("TRELLIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("TRELLIS_PORT", "17317")),
    )


def _run_ui() -> None:
    """Run the visualizer UI + API on TRELLIS_HOST:TRELLIS_PORT."""
    if getattr(sys, "frozen", False):
        try:
            from src.trellis.launcher import open_browser, setup_environment

            setup_environment()
            open_browser()
        except ImportError:
            pass

    import uvicorn

    app = create_ui_app(create_api_app(VERSION), VERSION)
    _require_key_for_network_bind()
    uvicorn.run(
        app,
        host=os.environ.get("TRELLIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("TRELLIS_PORT", "17317")),
    )


def _cli_sync(argv: list) -> int:
    """Headless `trellis sync` subcommand: index a repo and exit.

    Lets agents sync a project in a background shell when the MCP client's
    tool timeout is shorter than the sync (first index takes 5-15 min).
    Prints a heartbeat every 30s and a JSON summary at the end.
    """
    import threading

    parser = argparse.ArgumentParser(
        prog="trellis sync",
        description="Index a repository into the code graph and exit.",
    )
    parser.add_argument("repo_path", help="Path to the repository root")
    parser.add_argument(
        "--project-id", default="", help="Project id to register for this repo"
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Incremental re-index instead of a full rebuild",
    )
    args = parser.parse_args(argv)

    try:
        from src.trellis.launcher import setup_environment

        setup_environment()
    except ImportError:
        pass

    repo = Path(args.repo_path).resolve()
    if not repo.is_dir():
        print(f"error: '{args.repo_path}' is not a directory", file=sys.stderr)
        return 2

    from src.trellis import CodeGraphBridge
    from src.trellis.utils import register_project, write_sync_status

    register_project(args.project_id, repo)
    job = core.new_sync_job("cli")
    write_sync_status(args.project_id or repo.name, job)
    bridge = CodeGraphBridge(str(repo))
    print(f"Indexing {repo} ...", flush=True)

    done = threading.Event()

    def _heartbeat() -> None:
        while not done.wait(30):
            print("still indexing...", flush=True)

    threading.Thread(target=_heartbeat, daemon=True).start()

    try:
        if args.incremental:
            sync_result = bridge.incremental_sync()
        else:
            sync_result = bridge.sync_project()
        health = bridge.health_check()
    except Exception as e:
        job["state"] = "failed"
        job["finished_at"] = core.now_iso()
        job["detail"] = str(e)
        write_sync_status(args.project_id or repo.name, job)
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        done.set()

    success = (
        sync_result.get("success", False)
        if isinstance(sync_result, dict)
        else bool(sync_result)
    )
    job["state"] = "completed" if success else "failed"
    job["finished_at"] = core.now_iso()
    job["detail"] = "" if success else "sync exited without success"
    job["nodes"] = health.get("nodes_count", 0)
    job["files"] = health.get("files_count", 0)
    write_sync_status(args.project_id or repo.name, job)
    print(
        json.dumps(
            {
                "success": success,
                "nodes": job["nodes"],
                "files": job["files"],
            }
        ),
        flush=True,
    )
    return 0 if success else 1


def main() -> None:
    """Dispatch to the requested component (default: mcp)."""
    argv = sys.argv[1:]

    # Headless sync CLI: `trellis.exe sync <repo_path> [--project-id <id>]
    # [--incremental]` — runs outside the MCP server so agents can wait out
    # a long first sync in a background shell.
    if argv and argv[0] == "sync":
        sys.exit(_cli_sync(argv[1:]))

    parser = argparse.ArgumentParser(
        prog="trellis",
        description="Trellis: graph-based code analysis and knowledge management.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("mcp", help="MCP server (stdio default; HTTP via TRELLIS_TRANSPORT)")
    sub.add_parser("api", help="REST API only (FastAPI, /docs)")
    sub.add_parser("ui", help="Visualizer UI + API in one process")
    args = parser.parse_args(argv)

    if args.command in (None, "mcp"):
        _run_mcp()
    elif args.command == "api":
        _run_api()
    elif args.command == "ui":
        _run_ui()


if __name__ == "__main__":
    main()
