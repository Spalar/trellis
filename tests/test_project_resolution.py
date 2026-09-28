"""Tests for project-id resolution against the registry."""

from __future__ import annotations

import server


def _patch_registry(monkeypatch, mapping):
    monkeypatch.setattr(
        "src.trellis.utils.list_registered_projects", lambda: mapping
    )


def test_registered_project_resolves_from_unrelated_cwd(monkeypatch, tmp_path):
    """A registered project_id resolves to its recorded repo path even when
    the server runs from an unrelated cwd (no CWD-relative/sibling match)."""
    repo = tmp_path / "repos" / "Alpha"
    repo.mkdir(parents=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    _patch_registry(monkeypatch, {"Alpha": str(repo)})
    monkeypatch.chdir(cwd)

    assert server._resolve_project_path("Alpha") == str(repo)


def test_registered_project_wins_over_cwd_subdir(monkeypatch, tmp_path):
    """A stray same-named subdir in cwd must not hijack a registered
    project_id (previously candidate 1 beat the registry)."""
    real_repo = tmp_path / "real-alpha"
    real_repo.mkdir()
    cwd = tmp_path / "work"
    decoy = cwd / "Alpha"
    decoy.mkdir(parents=True)
    _patch_registry(monkeypatch, {"Alpha": str(real_repo)})
    monkeypatch.chdir(cwd)

    assert server._resolve_project_path("Alpha") == str(real_repo)


def test_unregistered_falls_back_to_heuristics(monkeypatch, tmp_path):
    """Unknown ids keep the old behavior: CWD-relative match, else raw id."""
    cwd = tmp_path / "work"
    sub = cwd / "Beta"
    sub.mkdir(parents=True)
    _patch_registry(monkeypatch, {})
    monkeypatch.chdir(cwd)

    assert server._resolve_project_path("Beta") == str(sub)
    assert server._resolve_project_path("Gamma") == "Gamma"


def test_registered_stale_path_falls_through(monkeypatch, tmp_path):
    """A registered path that no longer exists must not win over heuristics."""
    cwd = tmp_path / "work"
    sub = cwd / "Delta"
    sub.mkdir(parents=True)
    _patch_registry(monkeypatch, {"Delta": str(tmp_path / "gone")})
    monkeypatch.chdir(cwd)

    assert server._resolve_project_path("Delta") == str(sub)
