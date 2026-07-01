"""Tests for bridge feature-centric helpers."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.trellis.bridge import CodeGraphBridge


@pytest.fixture
def bridge_project(monkeypatch):
    """Create a temporary project with a project.md and code-graph DB."""
    data_dir = tempfile.mkdtemp(prefix="trellis-data-")
    project_dir = tempfile.mkdtemp(prefix="trellis-project-")
    monkeypatch.setenv("TRELLIS_DATA_DIR", data_dir)

    project_path = Path(project_dir)
    (project_path / "project.md").write_text(
        """
## Feature: Icons

Icon editing feature.

### Files
- src/ui/icon.js
- src/component/icon.js

## Feature: Auth

Authentication.

### Files
- src/auth/**
""",
        encoding="utf-8",
    )

    # Build a fake code-graph DB
    from src.trellis.utils import get_code_graph_path

    code_graph_dir = get_code_graph_path(project_path)
    code_graph_dir.mkdir(parents=True, exist_ok=True)
    db_path = code_graph_dir / "index.db"

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)
    """
    )
    cursor.execute(
        """
        CREATE TABLE nodes (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            type TEXT,
            start_line INTEGER,
            end_line INTEGER
        )
    """
    )
    files = [
        (1, "src/ui/icon.js"),
        (2, "src/component/icon.js"),
        (3, "src/auth/login.js"),
        (4, "src/util.js"),
    ]
    cursor.executemany("INSERT INTO files (id, path) VALUES (?, ?)", files)
    nodes = [
        (1, 1, "render", "Icon.render", "method", 10, 20),
        (2, 1, "select", "Icon.select", "method", 21, 30),
        (3, 2, "draw", "IconComponent.draw", "method", 5, 15),
        (4, 3, "login", "Auth.login", "method", 1, 10),
        (5, 4, "helper", "helper", "function", 1, 5),
    ]
    cursor.executemany(
        """
        INSERT INTO nodes (id, file_id, name, qualified_name, type, start_line, end_line)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        nodes,
    )
    conn.commit()
    conn.close()

    yield project_path

    try:
        import shutil

        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(project_dir, ignore_errors=True)
    except Exception:
        pass


def test_get_feature_functions(bridge_project):
    """Bridge returns all functions in feature file patterns."""
    bridge = CodeGraphBridge(str(bridge_project))
    funcs = bridge.get_feature_functions("Icons")
    names = {f["qualified_name"] for f in funcs}
    assert names == {"Icon.render", "Icon.select", "IconComponent.draw"}


def test_search_features(bridge_project):
    """Bridge searches project.md features by name."""
    bridge = CodeGraphBridge(str(bridge_project))
    results = bridge.search_features("icon")
    assert len(results) == 1
    assert results[0]["feature_name"] == "Icons"


def test_find_feature_for_function(bridge_project):
    """Bridge maps a file path to its owning feature."""
    bridge = CodeGraphBridge(str(bridge_project))
    assert bridge.find_feature_for_function("src/ui/icon.js") == "Icons"
    assert bridge.find_feature_for_function("src/auth/login.js") == "Auth"
    assert bridge.find_feature_for_function("src/util.js") is None


def test_get_feature_info(bridge_project):
    """Bridge aggregates feature info from spec and code graph."""
    bridge = CodeGraphBridge(str(bridge_project))
    info = bridge.get_feature_info("Icons")

    assert info["feature_name"] == "Icons"
    assert info["found_in_spec"] is True
    assert info["description"] == "Icon editing feature."
    assert info["functions_count"] == 3
    assert len(info["functions_sample"]) == 3
    assert len(info["hot_functions"]) <= 3
