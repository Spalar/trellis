"""Shared utilities for Trellis."""

import os
import shutil
from pathlib import Path


def get_trellis_data_dir() -> Path:
    """Get the trellis data directory for storing project indexes.

    Uses TRELLIS_DATA_DIR environment variable if set, otherwise ~/.trellis
    """
    data_dir = os.environ.get("TRELLIS_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().resolve()
    return Path.home() / ".trellis"


def _is_junction(path: Path) -> bool:
    """Check if a path is a Windows junction."""
    if os.name != "nt":
        return False
    try:
        import stat

        st = os.lstat(path)
        return stat.S_ISDIR(st.st_mode) and st.st_nlink > 1
    except (OSError, AttributeError):
        return False


def _create_windows_junction(link_path: Path, target_path: Path) -> bool:
    """Create a Windows directory junction using mklink /J.

    Junctions don't require admin privileges on Windows.
    Returns True if successful.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
            capture_output=True,
            text=True,
            shell=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_code_graph_path(project_path: Path) -> Path:
    """Get the path where .code-graph should be stored.

    By default, stores in trellis data directory (~/.trellis/projects/{name}/.code-graph)
    to avoid polluting project directories. Creates a symlink/junction in the project
    directory for compatibility with code-graph-mcp.

    Args:
        project_path: Path to the project repository

    Returns:
        Path to the .code-graph directory
    """
    project_path = project_path.resolve()
    project_id = project_path.name

    # Trellis data location
    trellis_data = get_trellis_data_dir()
    code_graph_dir = trellis_data / "projects" / project_id / ".code-graph"
    code_graph_dir.mkdir(parents=True, exist_ok=True)

    # Link in project directory
    project_code_graph = project_path / ".code-graph"

    if not project_code_graph.exists():
        # Try to create a symlink first
        try:
            project_code_graph.symlink_to(code_graph_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            # On Windows, try junction instead
            if os.name == "nt":
                if _create_windows_junction(project_code_graph, code_graph_dir):
                    pass  # Junction created successfully
                else:
                    # Fallback: use project directory directly
                    return project_code_graph
            else:
                # Fallback: use project directory directly
                return project_code_graph
    elif project_code_graph.is_symlink() or (
        os.name == "nt" and _is_junction(project_code_graph)
    ):
        # Ensure link points to correct location
        try:
            if project_code_graph.is_symlink():
                current_target = project_code_graph.readlink()
                if current_target != code_graph_dir:
                    project_code_graph.unlink()
                    project_code_graph.symlink_to(
                        code_graph_dir, target_is_directory=True
                    )
            # For junctions, just verify it resolves correctly
        except OSError:
            pass
    elif project_code_graph.is_dir():
        # Legacy: .code-graph exists in project directory
        # Migrate to trellis data directory
        if not code_graph_dir.exists():
            try:
                shutil.copytree(project_code_graph, code_graph_dir)
            except (PermissionError, OSError):
                # Can't copy (files locked), use project directory
                return project_code_graph
        # Try to replace with link
        try:
            shutil.rmtree(project_code_graph)
            project_code_graph.symlink_to(code_graph_dir, target_is_directory=True)
        except (OSError, NotImplementedError, PermissionError):
            if os.name == "nt":
                if not _create_windows_junction(project_code_graph, code_graph_dir):
                    # Can't create link, use project directory
                    return project_code_graph
            else:
                # Can't create link, use project directory
                return project_code_graph

    return code_graph_dir


def get_notes_path(project_path: Path) -> Path:
    """Get the path where knowledge notes should be stored.

    Stores notes alongside the code-graph index in the trellis data directory
    (~/.trellis/projects/{id}/.trellis/notes) to avoid polluting project
    directories.

    Args:
        project_path: Path to the project repository

    Returns:
        Path to the notes directory
    """
    code_graph_dir = get_code_graph_path(project_path)
    notes_dir = code_graph_dir.parent / ".trellis" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    return notes_dir


def resolve_code_graph_db(project_path: str) -> Path:
    """Resolve the path to the code-graph SQLite database.

    This is the main entry point for finding the index.db file.

    Args:
        project_path: Path to the project repository

    Returns:
        Path to index.db
    """
    code_graph_path = get_code_graph_path(Path(project_path))
    return code_graph_path / "index.db"
