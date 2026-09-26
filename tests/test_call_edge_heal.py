"""Tests for per-file call-edge drift detection in CodeGraphBridge."""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from src.trellis.bridge import CodeGraphBridge
from src.trellis.utils import get_code_graph_path


@pytest.fixture
def drift_project(monkeypatch):
    """Temp project with a real on-disk .py file and a fake code-graph DB."""
    data_dir = tempfile.mkdtemp(prefix="trellis-data-")
    project_dir = tempfile.mkdtemp(prefix="trellis-project-")
    monkeypatch.setenv("TRELLIS_DATA_DIR", data_dir)

    project_path = Path(project_dir)
    (project_path / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")

    code_graph_dir = get_code_graph_path(project_path)
    code_graph_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(code_graph_dir / "index.db"))
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute(
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, type TEXT)"
    )
    conn.execute(
        "CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, relation TEXT, metadata TEXT)"
    )
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'mod.py')")
    conn.execute(
        "INSERT INTO nodes (id, file_id, name, type) VALUES (1, 1, 'f', 'function')"
    )
    conn.commit()
    conn.close()

    yield project_path

    import shutil

    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(project_dir, ignore_errors=True)


def test_zero_outgoing_edges_in_edited_file_triggers_heal(drift_project, monkeypatch):
    bridge = CodeGraphBridge(str(drift_project))
    healed: list[bool] = []
    monkeypatch.setattr(bridge, "_heal_custom_edges", lambda: healed.append(True))

    bridge._last_call_edge_check = 0
    bridge._last_call_edge_heal = time.time() - 3600  # file mtime is newer
    bridge._ensure_call_edges()

    assert healed == [True]


def test_zero_outgoing_edges_in_unedited_file_is_ignored(drift_project, monkeypatch):
    # A global edge count of 0 means "global wipe" regardless of mtimes, so
    # give the DB an edge from a second file to isolate the per-file gate.
    (drift_project / "mod2.py").write_text("def g():\n    pass\n", encoding="utf-8")
    db_path = get_code_graph_path(drift_project) / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO files (id, path) VALUES (2, 'mod2.py')")
    conn.execute(
        "INSERT INTO nodes (id, file_id, name, type) VALUES (2, 2, 'g', 'function')"
    )
    conn.execute(
        "INSERT INTO edges (source_id, target_id, relation) VALUES (2, 2, 'calls')"
    )
    conn.commit()
    conn.close()

    bridge = CodeGraphBridge(str(drift_project))
    healed: list[bool] = []
    monkeypatch.setattr(bridge, "_heal_custom_edges", lambda: healed.append(True))

    bridge._last_call_edge_check = 0
    bridge._last_call_edge_heal = time.time() + 3600  # baseline after any mtime
    bridge._ensure_call_edges()

    assert healed == []


def test_existing_edges_prevent_heal(drift_project, monkeypatch):
    db_path = get_code_graph_path(drift_project) / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO edges (source_id, target_id, relation) VALUES (1, 1, 'calls')"
    )
    conn.commit()
    conn.close()

    bridge = CodeGraphBridge(str(drift_project))
    healed: list[bool] = []
    monkeypatch.setattr(bridge, "_heal_custom_edges", lambda: healed.append(True))

    bridge._last_call_edge_check = 0
    bridge._last_call_edge_heal = time.time() - 3600
    bridge._ensure_call_edges()

    assert healed == []
