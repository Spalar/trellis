"""Tests for the Trellis knowledge graph."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.trellis.knowledge_graph import NoteGraph


@pytest.fixture
def notes_project(monkeypatch):
    """Create a temporary project and isolated trellis data directory."""
    data_dir = tempfile.mkdtemp(prefix="trellis-data-")
    project_dir = tempfile.mkdtemp(prefix="trellis-project-")
    monkeypatch.setenv("TRELLIS_DATA_DIR", data_dir)

    yield Path(project_dir)

    # Cleanup
    try:
        import shutil

        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(project_dir, ignore_errors=True)
    except Exception:
        pass


def test_notes_stored_in_trellis_data_dir(notes_project):
    """Notes must live in ~/.trellis (or configured data dir), not the project."""
    graph = NoteGraph(str(notes_project))
    graph.save_note("hello", "content")

    assert graph.notes_dir.exists()
    assert (graph.notes_dir / "hello.md").exists()
    # Must not pollute the project directory
    assert not (notes_project / ".trellis" / "notes" / "hello.md").exists()


def test_save_and_get_note(notes_project):
    graph = NoteGraph(str(notes_project))
    note = graph.save_note(
        "architecture",
        "# Architecture\n\nUses [[database]] and @load_data.",
        title="Architecture Overview",
        tags=["feature"],
    )

    assert note.id == "architecture"
    assert note.title == "Architecture Overview"
    assert note.tags == ["feature"]
    assert "database" in note.links
    assert "load_data" in note.mentions

    fetched = graph.get_note("architecture")
    assert fetched is not None
    assert fetched.title == note.title


def test_delete_note(notes_project):
    graph = NoteGraph(str(notes_project))
    graph.save_note("temp", "temporary note")
    assert graph.get_note("temp") is not None

    assert graph.delete_note("temp") is True
    assert graph.get_note("temp") is None
    assert graph.delete_note("temp") is False


def test_backlinks(notes_project):
    graph = NoteGraph(str(notes_project))
    graph.save_note("a", "See [[b]].")
    graph.save_note("b", "Note B.")

    assert graph.get_backlinks("b") == ["a"]
    assert graph.get_backlinks("a") == []


def test_build_graph_filters_phantom_links_and_mentions(notes_project, monkeypatch):
    """Only edges to existing notes and real code symbols should be emitted."""
    graph = NoteGraph(str(notes_project))

    # Create a fake code-graph DB with one function so we can validate mentions.
    from src.trellis.utils import get_code_graph_path

    code_graph_dir = get_code_graph_path(notes_project)
    code_graph_dir.mkdir(parents=True, exist_ok=True)
    db_path = code_graph_dir / "index.db"

    import sqlite3

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
    cursor.execute("INSERT INTO files (id, path) VALUES (1, 'src/app.py')")
    cursor.execute(
        """
        INSERT INTO nodes (id, file_id, name, qualified_name, type, start_line, end_line)
        VALUES (1, 1, 'load_data', 'App.load_data', 'method', 10, 20)
    """
    )
    conn.commit()
    conn.close()

    graph.save_note(
        "architecture",
        "Uses [[existing-note]] and @load_data. Also [[missing-note]] and @fake_func.",
    )
    graph.save_note("existing-note", "I exist.")

    data = graph.build_graph(include_code=True)

    # Should contain note nodes + one code node
    note_nodes = {n["id"] for n in data["nodes"] if n["id"].startswith("note:")}
    code_nodes = {n["id"] for n in data["nodes"] if n["id"].startswith("func:")}
    assert note_nodes == {"note:architecture", "note:existing-note"}
    assert code_nodes == {"func:App.load_data"}

    # Only valid edges
    edges = {(e["source"], e["target"], e["type"]) for e in data["edges"]}
    assert ("note:architecture", "note:existing-note", "links_to") in edges
    assert ("note:architecture", "func:load_data", "mentions") in edges
    assert ("note:architecture", "note:missing-note", "links_to") not in edges
    assert ("note:architecture", "func:fake_func", "mentions") not in edges

    stats = data["stats"]
    assert len(stats["unresolved_links"]) == 1
    assert stats["unresolved_links"][0]["target"] == "missing-note"
    assert len(stats["unresolved_mentions"]) == 1
    assert stats["unresolved_mentions"][0]["target"] == "fake_func"


def test_legacy_notes_migration(notes_project, monkeypatch):
    """Notes stored in the old project/.trellis/notes location should migrate."""
    legacy_dir = notes_project / ".trellis" / "notes"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "legacy.md").write_text("# Legacy\n\ncontent", encoding="utf-8")

    # Force a fresh data dir
    data_dir = tempfile.mkdtemp(prefix="trellis-data-")
    monkeypatch.setenv("TRELLIS_DATA_DIR", data_dir)

    graph = NoteGraph(str(notes_project))
    assert graph.get_note("legacy") is not None
    assert (graph.notes_dir / "legacy.md").exists()
    assert not (legacy_dir / "legacy.md").exists()


def test_link_alias_resolution(notes_project):
    """[[Feature: Code Graph Bridge]] should resolve to note feature-code-graph-bridge."""
    graph = NoteGraph(str(notes_project))
    graph.save_note(
        "feature-code-graph-bridge",
        "# Feature: Code Graph Bridge\n\nDetails.",
        title="Feature: Code Graph Bridge",
        tags=["feature"],
    )
    graph.save_note(
        "architecture",
        "See [[Feature: Code Graph Bridge]] and [[Code Graph Bridge]].",
        title="Architecture",
    )

    data = graph.build_graph(include_code=False)
    edges = {(e["source"], e["target"], e["type"]) for e in data["edges"]}
    assert (
        "note:architecture",
        "note:feature-code-graph-bridge",
        "links_to",
    ) in edges

    assert graph.get_backlinks("feature-code-graph-bridge") == ["architecture"]
    assert len(data["stats"]["unresolved_links"]) == 0


def test_mentions_filter_prose_placeholders(notes_project):
    """@Function and @project should be ignored; real code refs kept."""
    note = type("Note", (), {})()
    note.content = (
        "Uses @CodeGraphBridge, @CodeGraphBridge.analyze_impact, @trellis_sync, "
        "@bridge.py, @API, and also @Function, @project, @tests in prose."
    )
    from src.trellis.knowledge_graph import Note as RealNote

    real_note = RealNote(id="test", title="Test", content=note.content, path="test.md")
    mentions = real_note.mentions

    assert "CodeGraphBridge" in mentions
    assert "CodeGraphBridge.analyze_impact" in mentions
    assert "trellis_sync" in mentions
    assert "bridge.py" in mentions
    assert "API" in mentions
    assert "Function" not in mentions
    assert "project" not in mentions
    assert "tests" not in mentions


def test_search_features(notes_project):
    """Feature notes should be searchable by feature name."""
    graph = NoteGraph(str(notes_project))
    graph.save_note(
        "feature-icons",
        "# Feature: Icons\n\nAdd and edit icons.",
        title="Feature: Icons",
        tags=["feature"],
    )
    graph.save_note("other", "Unrelated note.")

    results = graph.search_features("icons")
    assert len(results) == 1
    assert results[0].id == "feature-icons"
