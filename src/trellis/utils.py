"""Shared utilities for Trellis."""

import json
import os
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


def get_code_graph_path(project_path: Path) -> Path:
    """Get the path where .code-graph should be stored.

    The index lives in the project directory itself (`<repo>/.code-graph`) —
    code-graph-mcp resolves the DB path from the project root and refuses
    symlinked directories (v0.37+). Legacy symlinks/junctions into the
    trellis data directory are removed; a stale index is wiped and rebuilt
    by code-graph-mcp on first open anyway (INDEX_VERSION bump).

    Args:
        project_path: Path to the project repository

    Returns:
        Path to the .code-graph directory
    """
    project_path = project_path.resolve()
    code_graph_dir = project_path / ".code-graph"

    if code_graph_dir.is_symlink():
        try:
            code_graph_dir.unlink()
        except OSError:
            pass  # locked: mkdir below tolerates the existing link
    elif os.name == "nt" and _is_junction(code_graph_dir):
        try:
            code_graph_dir.rmdir()  # removing a junction removes only the link
        except OSError:
            pass

    code_graph_dir.mkdir(parents=True, exist_ok=True)
    if code_graph_dir.is_symlink() or _is_junction(code_graph_dir):
        raise RuntimeError(
            f"Refusing to use symlinked/junction .code-graph directory: {code_graph_dir}. "
            "Remove the link and re-sync the project."
        )
    _record_project(project_path)
    return code_graph_dir


def _record_project(project_path: Path) -> None:
    """Record a project in the trellis data dir so the UI can list it.

    Writes ~/.trellis/projects/<name>/project.json with the repo path. The
    code graph itself no longer lives there (see get_code_graph_path), but
    notes and this registry stay centralized.
    """
    try:
        project_dir = get_trellis_data_dir() / "projects" / project_path.name
        project_dir.mkdir(parents=True, exist_ok=True)
        marker = project_dir / "project.json"
        payload = {"path": str(project_path)}
        if not marker.exists() or marker.read_text(encoding="utf-8") != json.dumps(
            payload
        ):
            marker.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def list_registered_projects() -> dict:
    """Return registered projects as {project_id: repo_path}.

    Reads the registry markers written by _record_project. Used by the UI
    project list and to validate that an absolute path is a known project
    before a bridge/indexer is pointed at it.
    """
    projects = {}
    projects_dir = get_trellis_data_dir() / "projects"
    if projects_dir.exists():
        for item in projects_dir.iterdir():
            marker = item / "project.json"
            if not item.is_dir() or not marker.exists():
                continue
            try:
                repo_path = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])
            except (json.JSONDecodeError, KeyError, OSError):
                continue
            projects[item.name] = str(repo_path)
    return projects


def get_notes_path(project_path: Path) -> Path:
    """Get the path where knowledge notes should be stored.

    Notes stay centralized in the trellis data directory
    (~/.trellis/projects/<id>/.trellis/notes) so they are shared across
    checkouts of the same repo and never pollute the project directory.

    Args:
        project_path: Path to the project repository

    Returns:
        Path to the notes directory
    """
    project_path = project_path.resolve()
    notes_dir = get_trellis_data_dir() / "projects" / project_path.name / ".trellis" / "notes"
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
