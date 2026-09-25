"""Tests for SpecManager project.md location handling."""

from __future__ import annotations

from pathlib import Path

from spec_manager import SpecManager


def _resolver(repo: Path):
    return lambda project_id: str(repo / project_id)


def test_load_prefers_repo_root(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "project.md").write_text("# Repo Spec", encoding="utf-8")

    manager = SpecManager(base_dir=str(tmp_path / "data"), project_resolver=_resolver(tmp_path))
    spec = manager.load_spec("myrepo")

    assert spec is not None
    assert spec.content == "# Repo Spec"
    assert spec.source_path == str(repo / "project.md")


def test_save_writes_to_repo_root(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()

    manager = SpecManager(base_dir=str(tmp_path / "data"), project_resolver=_resolver(tmp_path))
    path = manager.save_spec("myrepo", "# New Spec")

    assert path == repo / "project.md"
    assert (repo / "project.md").read_text(encoding="utf-8") == "# New Spec"


def test_save_repo_root_is_file_feature_tools_parse(tmp_path):
    """Saved spec must be at <repo>/project.md — the path the parser reads."""
    from src.trellis.feature_impact import ProjectContextParser

    repo = tmp_path / "myrepo"
    repo.mkdir()

    manager = SpecManager(base_dir=str(tmp_path / "data"), project_resolver=_resolver(tmp_path))
    manager.save_spec(
        "myrepo",
        "## Feature: Auth\n\nHandles login.\n\n### Files\n- src/auth/**\n",
    )

    parser = ProjectContextParser(str(repo))
    features = parser.get_all_features()
    assert "Auth" in features
    assert features["Auth"].file_patterns == ["src/auth/**"]


def test_load_falls_back_to_data_dir(tmp_path):
    """Unresolved projects still find their legacy data-dir copy."""
    repo = tmp_path / "data"
    project_dir = repo / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "project.md").write_text("# Data Dir Spec", encoding="utf-8")

    manager = SpecManager(base_dir=str(repo))
    spec = manager.load_spec("myproject")

    assert spec is not None
    assert spec.content == "# Data Dir Spec"


def test_save_falls_back_to_data_dir_without_resolver(tmp_path):
    manager = SpecManager(base_dir=str(tmp_path / "data"))
    path = manager.save_spec("myproject", "# Spec")

    assert path == tmp_path / "data" / "myproject" / "project.md"
    assert path.read_text(encoding="utf-8") == "# Spec"
