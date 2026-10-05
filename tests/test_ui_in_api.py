"""Tests for UI-in-API serving and the trusted-mode UI token-leak fix.

The API app serves the visualizer at "/" by default (TRELLIS_UI=off opts
out). On loopback binds "/" stays public and embeds the launch token; on
trusted-network binds "/" requires credentials — otherwise the embedded
token (the API key) would leak to anyone reaching the port. Browsers cannot
set headers on navigation, so "/" accepts ?token=<key> there.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from src.trellis import core
from src.trellis.api import create_api_app

LOCAL_HOST = {"Host": "127.0.0.1"}


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """API app (with LocalHttpGuard middleware) against an isolated data dir."""
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    return TestClient(create_api_app("0.3.0-test"))


def test_api_serves_ui_by_default(api_client):
    resp = api_client.get("/", headers=LOCAL_HOST)
    assert resp.status_code == 200
    assert "window.TRELLIS_TOKEN" in resp.text

    resp = api_client.get("/vendor/d3.v7.min.js", headers=LOCAL_HOST)
    assert resp.status_code == 200


def test_ui_opt_out_with_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRELLIS_UI", "off")
    client = TestClient(create_api_app("0.3.0-test"))

    assert client.get("/", headers=LOCAL_HOST).status_code == 404
    assert client.get("/health", headers=LOCAL_HOST).status_code == 200


def test_trusted_mode_ui_requires_token(monkeypatch, tmp_path):
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRELLIS_HOST", "0.0.0.0")
    monkeypatch.setenv("TRELLIS_API_KEY", "net-secret")
    monkeypatch.setattr(core, "http_token", "net-secret")
    client = TestClient(create_api_app("0.3.0-test"))

    # Without credentials the page (which embeds the key) must not be served.
    assert client.get("/", headers=LOCAL_HOST).status_code == 401
    assert client.get("/?token=wrong", headers=LOCAL_HOST).status_code == 401

    # Header auth (programmatic clients) and ?token= (browsers) both work.
    resp = client.get(
        "/", headers={**LOCAL_HOST, "Authorization": "Bearer net-secret"}
    )
    assert resp.status_code == 200
    assert "net-secret" in resp.text

    resp = client.get("/?token=net-secret", headers=LOCAL_HOST)
    assert resp.status_code == 200
    assert "net-secret" in resp.text

    # Vendor assets stay public static files in both modes.
    assert client.get("/vendor/d3.v7.min.js", headers=LOCAL_HOST).status_code == 200
