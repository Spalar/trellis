"""Tests for re-linking Trellis's custom edges after upstream re-indexes files."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.trellis.bridge import CodeGraphBridge
from src.trellis.utils import get_code_graph_path


@pytest.fixture
def project(monkeypatch):
    """Temp project with a fake code-graph DB holding one .py and one .js file."""
    data_dir = tempfile.mkdtemp(prefix="trellis-data-")
    project_dir = tempfile.mkdtemp(prefix="trellis-project-")
    monkeypatch.setenv("TRELLIS_DATA_DIR", data_dir)

    project_path = Path(project_dir)
    db_path = get_code_graph_path(project_path) / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, indexed_at INTEGER)")
    conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, file_id INTEGER)")
    conn.executemany("INSERT INTO nodes (id, file_id) VALUES (?, ?)", [(1, 1), (2, 2)])
    conn.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER)")
    conn.execute("INSERT INTO edges (source_id, target_id) VALUES (1, 2)")
    conn.executemany(
        "INSERT INTO files (id, path, indexed_at) VALUES (?, ?, ?)",
        [(1, "mod.py", 100), (2, "app.js", 100)],
    )
    conn.commit()
    conn.close()

    yield project_path, db_path

    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(project_dir, ignore_errors=True)


def _bridge(project_path, monkeypatch, fail=()):
    bridge = CodeGraphBridge(str(project_path))
    calls: list = []

    def fake_heal(python=True, js=True):
        calls.append((python, js))
        return {k for k in fail if (k == "py" and python) or (k == "js" and js)}

    monkeypatch.setattr(bridge, "_heal_custom_edges", fake_heal)
    return bridge, calls


def _reindex(db_path, path, indexed_at):
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE files SET indexed_at = ? WHERE path = ?", (indexed_at, path))
    conn.commit()
    conn.close()


def test_first_check_links_everything_then_stays_quiet(project, monkeypatch):
    project_path, _ = project
    bridge, calls = _bridge(project_path, monkeypatch)

    bridge._ensure_call_edges()
    bridge._ensure_call_edges()

    assert calls == [(True, True)]


def test_reindexed_python_file_relinks_python_only(project, monkeypatch):
    project_path, db_path = project
    bridge, calls = _bridge(project_path, monkeypatch)
    bridge._ensure_call_edges()

    _reindex(db_path, "mod.py", 200)
    bridge._ensure_call_edges()

    assert calls[-1] == (True, False)


def test_reindexed_js_file_relinks_js_only(project, monkeypatch):
    project_path, db_path = project
    bridge, calls = _bridge(project_path, monkeypatch)
    bridge._ensure_call_edges()

    _reindex(db_path, "app.js", 200)
    bridge._ensure_call_edges()

    assert calls[-1] == (False, True)


def test_failed_relink_is_retried_after_backoff(project, monkeypatch):
    project_path, _ = project
    bridge, calls = _bridge(project_path, monkeypatch, fail=("js",))

    bridge._ensure_call_edges()
    bridge._ensure_call_edges()  # backing off: no retry yet
    assert calls == [(True, True)]

    bridge._retry_at = 0  # back-off elapsed
    bridge._ensure_call_edges()
    assert calls[-1] == (False, True)  # only the failed kind is retried


def test_state_survives_a_new_bridge(project, monkeypatch):
    project_path, _ = project
    first, _ = _bridge(project_path, monkeypatch)
    first._ensure_call_edges()

    second, calls = _bridge(project_path, monkeypatch)
    second._ensure_call_edges()

    assert calls == []


def test_reindex_within_the_same_second_is_detected(project, monkeypatch):
    """indexed_at is whole seconds; new node ids still reveal the re-index."""
    project_path, db_path = project
    bridge, calls = _bridge(project_path, monkeypatch)
    bridge._ensure_call_edges()

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM nodes WHERE file_id = 1")
    conn.execute("INSERT INTO nodes (id, file_id) VALUES (3, 1)")
    conn.commit()
    conn.close()
    bridge._ensure_call_edges()

    assert calls[-1] == (True, False)


def test_lost_edges_without_file_changes_relink(project, monkeypatch):
    """Same files and stamps, fewer edges: something wiped ours, so re-link."""
    project_path, db_path = project
    bridge, calls = _bridge(project_path, monkeypatch)
    bridge._ensure_call_edges()
    bridge._ensure_call_edges()
    assert len(calls) == 1  # unchanged index: the fingerprint short-circuits

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM edges")
    conn.commit()
    conn.close()
    bridge._ensure_call_edges()

    assert calls[-1] == (True, True)


def test_reindex_during_relink_is_not_absorbed(project, monkeypatch):
    """Upstream re-indexing while we re-link must trigger another re-link."""
    project_path, db_path = project
    bridge = CodeGraphBridge(str(project_path))
    calls: list = []

    def heal(python=True, js=True):
        calls.append((python, js))
        if len(calls) == 1:  # upstream re-indexes app.js mid-heal
            _reindex(db_path, "app.js", 300)
        return set()

    monkeypatch.setattr(bridge, "_heal_custom_edges", heal)
    bridge._ensure_call_edges()
    bridge._ensure_call_edges()

    assert calls == [(True, True), (False, True)]
