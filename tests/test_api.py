"""Tests for the FastAPI backend (src/trellis/api.py) and UI app (src/trellis/ui.py)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from src.trellis import core
from src.trellis.api import create_api_app
from src.trellis.ui import create_ui_app

LOCAL_HOST = {"Host": "127.0.0.1"}


def _auth_headers():
    return {"Host": "127.0.0.1", "X-Trellis-Token": core.http_token}


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """API app against an isolated trellis data dir."""
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    return TestClient(create_api_app("0.0.0-test"))


@pytest.fixture
def ui_client(monkeypatch, tmp_path):
    """UI app (visualizer + mounted API) against an isolated trellis data dir."""
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    return TestClient(create_ui_app(create_api_app("0.0.0-test"), "0.0.0-test"))


def test_health_is_ok(api_client):
    resp = api_client.get("/health", headers=LOCAL_HOST)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.0.0-test"}


def test_health_needs_no_token(api_client):
    # "/" and "/health" are exempt from the token check (page bootstrap),
    # but still require a local Host header.
    resp = api_client.get("/health", headers=LOCAL_HOST)
    assert resp.status_code == 200


def test_projects_returns_json_list(api_client):
    resp = api_client.get("/projects", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"projects": []}


def test_projects_registered_project_summary(api_client, tmp_path):
    from src.trellis.utils import register_project

    repo = tmp_path / "repos" / "demo"
    repo.mkdir(parents=True)
    register_project("demo", repo)

    resp = api_client.get("/projects", headers=_auth_headers())
    assert resp.status_code == 200
    projects = resp.json()["projects"]
    assert len(projects) == 1
    entry = projects[0]
    assert entry["id"] == "demo"
    assert entry["path"] == str(repo.resolve())
    assert entry["index_exists"] is False
    assert entry["nodes"] is None
    assert entry["files"] is None


def test_projects_requires_token(api_client):
    resp = api_client.get("/projects", headers=LOCAL_HOST)
    assert resp.status_code == 401


def test_projects_rejects_wrong_token(api_client):
    resp = api_client.get(
        "/projects",
        headers={"Host": "127.0.0.1", "X-Trellis-Token": "not-the-token"},
    )
    assert resp.status_code == 401


def test_projects_rejects_bearer_wrong_token(api_client):
    resp = api_client.get(
        "/projects",
        headers={"Host": "127.0.0.1", "Authorization": "Bearer not-the-token"},
    )
    assert resp.status_code == 401


def test_projects_bearer_token_accepted(api_client):
    resp = api_client.get(
        "/projects",
        headers={"Host": "127.0.0.1", "Authorization": f"Bearer {core.http_token}"},
    )
    assert resp.status_code == 200


def test_untrusted_host_rejected(api_client):
    resp = api_client.get("/projects", headers={"Host": "evil.example.com"})
    assert resp.status_code == 403


def test_untrusted_origin_rejected(api_client):
    resp = api_client.get(
        "/projects",
        headers={**_auth_headers(), "Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


def test_no_auth_mode_skips_token_check(api_client, monkeypatch):
    monkeypatch.setenv("TRELLIS_ALLOW_NO_AUTH", "true")
    resp = api_client.get("/projects", headers=LOCAL_HOST)
    assert resp.status_code == 200


def test_vendor_assets_served(api_client):
    resp = api_client.get("/vendor/d3.v7.min.js", headers=LOCAL_HOST)
    assert resp.status_code == 200
    # Filename validation rejects path-like or invalid names.
    resp = api_client.get("/vendor/a%20b.js", headers=LOCAL_HOST)
    assert resp.status_code == 400
    resp = api_client.get("/vendor/missing.js", headers=LOCAL_HOST)
    assert resp.status_code == 404


def test_ui_serves_visualizer_with_token(ui_client):
    resp = ui_client.get("/", headers=LOCAL_HOST)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'window.TRELLIS_TOKEN = "' in resp.text
    assert core.http_token in resp.text
    # Visualizer content is present (graph pane + same-origin baseHost default).
    assert "graph-pane" in resp.text
    assert "window.location.origin" in resp.text


def test_ui_mounts_api_routes(ui_client):
    resp = ui_client.get("/health", headers=LOCAL_HOST)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.0.0-test"}

    # Mounted API keeps its guard: no token -> 401.
    resp = ui_client.get("/projects", headers=LOCAL_HOST)
    assert resp.status_code == 401
    resp = ui_client.get("/projects", headers=_auth_headers())
    assert resp.status_code == 200
