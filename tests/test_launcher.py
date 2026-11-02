"""Bundled-release token handling."""

from __future__ import annotations

from src.trellis import launcher


def test_token_created_once_and_reused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRELLIS_API_KEY", "")  # registers it so the test restores it
    first = launcher.ensure_api_token(tmp_path)
    monkeypatch.setenv("TRELLIS_API_KEY", "")  # registers it so the test restores it
    second = launcher.ensure_api_token(tmp_path)

    assert first and first == second
    assert (tmp_path / launcher.TOKEN_FILE).read_text(encoding="utf-8") == first
    assert capsys.readouterr().out == ""  # stdout may be an MCP stdio channel


def test_existing_api_key_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("TRELLIS_API_KEY", "given")
    assert launcher.ensure_api_token(tmp_path) == "given"
    assert not (tmp_path / launcher.TOKEN_FILE).exists()


def test_bundle_no_longer_disables_auth(monkeypatch):
    import os
    import sys

    monkeypatch.delenv("TRELLIS_ALLOW_NO_AUTH", raising=False)
    monkeypatch.setattr(launcher, "get_bundle_dir", lambda: launcher.Path("."))
    saved_env, saved_path = dict(os.environ), list(sys.path)
    try:
        launcher.setup_environment()
        assert "TRELLIS_ALLOW_NO_AUTH" not in os.environ
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.path[:] = saved_path
