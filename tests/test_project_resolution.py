"""Tests for project-id resolution against the registry."""

from __future__ import annotations

from pathlib import Path

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
    """Unknown ids still resolve via CWD-relative match; a total unknown now
    raises instead of returning the raw id (which created phantom
    <cwd>/<id>/.code-graph directories)."""
    cwd = tmp_path / "work"
    sub = cwd / "Beta"
    sub.mkdir(parents=True)
    _patch_registry(monkeypatch, {})
    monkeypatch.chdir(cwd)

    assert server._resolve_project_path("Beta") == str(sub)
    import pytest

    with pytest.raises(ValueError, match="Unknown project 'Gamma'"):
        server._resolve_project_path("Gamma")


def test_unknown_id_suggests_close_registered_project(monkeypatch, tmp_path):
    """An unknown id close to a registered one gets a did-you-mean hint plus
    the registration instruction."""
    repo = tmp_path / "repos" / "desfut_vuegen"
    repo.mkdir(parents=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    _patch_registry(monkeypatch, {"desfut_vuegen": str(repo)})
    monkeypatch.chdir(cwd)

    import pytest

    with pytest.raises(ValueError) as exc_info:
        server._resolve_project_path("desfut-vuegen")
    message = str(exc_info.value)
    assert "Unknown project 'desfut-vuegen'" in message
    assert "Did you mean: desfut_vuegen" in message
    assert 'trellis_sync(project_id="desfut-vuegen"' in message


def test_unknown_id_without_close_match_has_register_hint(monkeypatch, tmp_path):
    """No close match: just the register hint, no did-you-mean."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    _patch_registry(monkeypatch, {"totally-unrelated": str(tmp_path / "x")})
    monkeypatch.chdir(cwd)

    import pytest

    with pytest.raises(ValueError) as exc_info:
        server._resolve_project_path("zzz-no-such-project")
    message = str(exc_info.value)
    assert "Did you mean" not in message
    assert "Register it first" in message


def test_registered_stale_path_falls_through(monkeypatch, tmp_path):
    """A registered path that no longer exists must not win over heuristics."""
    cwd = tmp_path / "work"
    sub = cwd / "Delta"
    sub.mkdir(parents=True)
    _patch_registry(monkeypatch, {"Delta": str(tmp_path / "gone")})
    monkeypatch.chdir(cwd)

    assert server._resolve_project_path("Delta") == str(sub)


# ------------------------------------------------------------------
# Registry writes (register_project / _record_project)
# ------------------------------------------------------------------


def test_register_project_maps_agent_project_id(monkeypatch, tmp_path):
    """trellis_sync's explicit registration records the agent's project_id
    pointing at the resolved repo, so later calls with that id hit the
    registry."""
    import json

    from src.trellis.utils import list_registered_projects, register_project

    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path))
    repo = tmp_path / "repos" / "desfut_vuegen"
    repo.mkdir(parents=True)

    register_project("desfut-vuegen", repo)

    registered = list_registered_projects()
    assert registered["desfut-vuegen"] == str(repo.resolve())
    # Basename key also usable (resolves to the same repo).
    assert Path(registered["desfut-vuegen"]) == repo.resolve()


def test_register_project_sanitizes_path_like_and_empty_ids(monkeypatch, tmp_path):
    """Empty ids and ids that look like paths fall back to the repo basename."""
    from src.trellis.utils import list_registered_projects, register_project

    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path))
    repo = tmp_path / "repos" / "desfut_vuegen"
    repo.mkdir(parents=True)

    register_project("", repo)
    register_project("  ", repo)
    register_project("some/path", repo)
    register_project(r"some\path", repo)
    register_project("K:/repos/other", repo)

    registered = list_registered_projects()
    assert set(registered) == {"desfut_vuegen"}
    assert registered["desfut_vuegen"] == str(repo.resolve())


def test_record_project_skips_mei_dirs(monkeypatch, tmp_path):
    """PyInstaller temp extraction dirs (_MEI*) must never be registered."""
    import json

    from src.trellis.utils import _record_project

    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path))

    mei = tmp_path / "_MEI642282"
    mei.mkdir()
    _record_project(mei)
    assert not (tmp_path / "projects" / "_MEI642282").exists()

    real = tmp_path / "realproject"
    real.mkdir()
    _record_project(real)
    marker = tmp_path / "projects" / "realproject" / "project.json"
    assert marker.exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["path"] == str(real)


# ------------------------------------------------------------------
# Sync status persistence (write_sync_status / read_sync_status)
# ------------------------------------------------------------------


def test_sync_status_roundtrip(monkeypatch, tmp_path):
    import json

    from src.trellis.utils import read_sync_status, write_sync_status

    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path))

    status = {
        "state": "running",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": None,
        "detail": "",
        "nodes": None,
        "files": None,
        "source": "mcp",
    }
    write_sync_status("my-project", status)

    marker = tmp_path / "projects" / "my-project" / "sync_status.json"
    assert marker.exists()
    # No temp file left behind after the atomic replace.
    assert not (tmp_path / "projects" / "my-project" / "sync_status.json.tmp").exists()
    assert json.loads(marker.read_text(encoding="utf-8")) == status
    assert read_sync_status("my-project") == status

    status["state"] = "completed"
    status["finished_at"] = "2026-01-01T00:10:00+00:00"
    status["nodes"] = 42
    write_sync_status("my-project", status)
    assert read_sync_status("my-project") == status


def test_sync_status_write_skipped_for_unsanitizable_ids(monkeypatch, tmp_path):
    """Empty or path-like ids have no safe registry key — nothing is written."""
    from src.trellis.utils import read_sync_status, write_sync_status

    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path))

    status = {"state": "running"}
    for bad_id in ("", "  ", "some/path", r"some\path", "K:/repos/x"):
        write_sync_status(bad_id, status)
    assert not (tmp_path / "projects").exists()
    assert read_sync_status("some/path") is None


def test_sync_status_read_returns_none_for_unknown_id(monkeypatch, tmp_path):
    from src.trellis.utils import read_sync_status

    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path))
    assert read_sync_status("never-written") is None
