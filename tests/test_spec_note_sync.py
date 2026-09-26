"""Tests for auto-syncing feature notes from project.md."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.trellis.knowledge_graph import NoteGraph
from src.trellis.spec_note_sync import AUTO_MARKER, feature_note_id, sync_feature_notes

SPEC = """## Project Info

An image editor. This section is free-form and ignored by the parser.

## Feature: Mask

Masking feature.

### Decisions
- MASK-001: Use overlay layer (because: keeps original pixels)
  - Constraint: Never mutate the source image
### Files
- src/mask.js
### Dependencies
- Feature: Graphics

## Feature: Graphics

Rendering.

- **Files**: src/graphics.js, src/canvas.js
"""


@pytest.fixture
def spec_project(monkeypatch):
    data_dir = tempfile.mkdtemp(prefix="trellis-data-")
    project_dir = tempfile.mkdtemp(prefix="trellis-project-")
    monkeypatch.setenv("TRELLIS_DATA_DIR", data_dir)

    project_path = Path(project_dir)
    (project_path / "project.md").write_text(SPEC, encoding="utf-8")

    yield project_path

    import shutil

    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(project_dir, ignore_errors=True)


def test_creates_note_per_feature(spec_project):
    result = sync_feature_notes(str(spec_project))
    assert result["features"] == 2
    assert result["created"] == 2
    assert result["updated"] == 0

    graph = NoteGraph(str(spec_project))
    note = graph.get_note("feature-mask")
    assert note is not None
    assert "feature" in note.tags
    assert AUTO_MARKER in note.content
    assert "MASK-001: Use overlay layer" in note.content
    assert "because: keeps original pixels" in note.content
    assert "Constraint: Never mutate the source" in note.content
    assert "`src/mask.js`" in note.content
    # dependency links into the doc graph
    assert "[[feature-graphics]]" in note.content


def test_second_sync_is_idempotent(spec_project):
    sync_feature_notes(str(spec_project))
    result = sync_feature_notes(str(spec_project))
    assert result["created"] == 0
    assert result["updated"] == 0


def test_spec_change_updates_marked_note(spec_project):
    sync_feature_notes(str(spec_project))
    spec_path = spec_project / "project.md"
    spec_path.write_text(SPEC.replace("Use overlay layer", "Use overlay layer v2"))

    result = sync_feature_notes(str(spec_project))
    assert result["updated"] == 1

    graph = NoteGraph(str(spec_project))
    assert "overlay layer v2" in graph.get_note("feature-mask").content


def test_agent_authored_note_is_not_overwritten(spec_project):
    graph = NoteGraph(str(spec_project))
    graph.save_note(
        "feature-mask",
        "Hand-written architecture deep dive. Do not clobber.",
        title="Feature: Mask",
        tags=["feature"],
    )

    result = sync_feature_notes(str(spec_project))
    assert result["skipped_agent_authored"] == 1

    note = NoteGraph(str(spec_project)).get_note("feature-mask")
    assert "Do not clobber" in note.content
    assert AUTO_MARKER not in note.content


def test_no_features_no_notes(spec_project):
    (spec_project / "project.md").write_text("## Overview\n\nJust prose.\n")
    result = sync_feature_notes(str(spec_project))
    assert result["features"] == 0
    assert result["created"] == 0


def test_feature_note_id_slug():
    assert feature_note_id("Mask") == "feature-mask"
    assert feature_note_id("Mask Tool v2") == "feature-mask-tool-v2"
    assert feature_note_id("Shapes & Icons") == "feature-shapes-icons"
