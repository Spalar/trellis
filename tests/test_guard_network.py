"""Tests for trusted-network mode of the HTTP guard (src/trellis/core.py).

Covers TRELLIS_TRUSTED_HOSTS allowlisting, trusted mode (non-loopback bind)
token enforcement, and the server startup refusal for keyless network binds.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import server
from src.trellis import core
from src.trellis.api import create_api_app


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """API app (with LocalHttpGuard middleware) against an isolated data dir."""
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    return TestClient(create_api_app("0.0.0-test"))


def test_loopback_default_rejects_nonlocal_host(api_client):
    # Existing hardening still holds without any trusted-network config.
    resp = api_client.get("/projects", headers={"Host": "evil.example.com"})
    assert resp.status_code == 403


def test_trusted_hosts_allowlist_extends_host_check(api_client, monkeypatch):
    monkeypatch.setenv("TRELLIS_TRUSTED_HOSTS", "trellis,example.internal")
    headers = {
        "Host": "trellis:17317",
        "X-Trellis-Token": core.http_token,
    }
    resp = api_client.get("/projects", headers=headers)
    assert resp.status_code == 200

    headers["Host"] = "example.internal"
    resp = api_client.get("/projects", headers=headers)
    assert resp.status_code == 200

    # Hostnames not in the allowlist are still rejected.
    resp = api_client.get(
        "/projects",
        headers={"Host": "evil.example.com", "X-Trellis-Token": core.http_token},
    )
    assert resp.status_code == 403


def test_trusted_hosts_allowlist_extends_origin_check(api_client, monkeypatch):
    monkeypatch.setenv("TRELLIS_TRUSTED_HOSTS", "trellis")
    resp = api_client.get(
        "/projects",
        headers={
            "Host": "127.0.0.1",
            "Origin": "http://trellis:17317",
            "X-Trellis-Token": core.http_token,
        },
    )
    assert resp.status_code == 200


@pytest.fixture
def trusted_client(monkeypatch, tmp_path):
    """Client in trusted-network mode: non-loopback bind + API key set."""
    monkeypatch.setenv("TRELLIS_HOST", "0.0.0.0")
    monkeypatch.setenv("TRELLIS_API_KEY", "test-key")
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    return TestClient(create_api_app("0.0.0-test"))


def test_trusted_mode_accepts_network_host_with_token(trusted_client):
    resp = trusted_client.get(
        "/projects",
        headers={"Host": "trellis:17317", "Authorization": "Bearer test-key"},
    )
    assert resp.status_code == 200


def test_trusted_mode_ignores_no_auth_and_always_requires_token(
    trusted_client, monkeypatch
):
    monkeypatch.setenv("TRELLIS_ALLOW_NO_AUTH", "true")
    # Wrong/non-local Host no longer 403s; the token check decides instead.
    resp = trusted_client.get("/projects", headers={"Host": "trellis:17317"})
    assert resp.status_code == 401


def test_trusted_mode_accepts_arbitrary_host_with_token(trusted_client):
    resp = trusted_client.get(
        "/projects",
        headers={"Host": "anything.example", "Authorization": "Bearer test-key"},
    )
    assert resp.status_code == 200


def test_trusted_mode_wrong_token_still_rejected(trusted_client):
    resp = trusted_client.get(
        "/projects",
        headers={"Host": "trellis:17317", "Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_trusted_network_mode_helper(monkeypatch):
    for host, expected in [
        ("127.0.0.1", False),
        ("localhost", False),
        ("::1", False),
        ("0.0.0.0", True),
        ("::", True),
        ("*", True),
        ("192.168.1.10", True),
        ("trellis", False),  # unresolvable hostname: conservative
    ]:
        monkeypatch.setenv("TRELLIS_HOST", host)
        assert core.trusted_network_mode() is expected, host
    monkeypatch.delenv("TRELLIS_HOST", raising=False)
    assert core.trusted_network_mode() is False


def test_startup_refuses_network_bind_without_api_key(monkeypatch, capsys):
    monkeypatch.delenv("TRELLIS_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        server._require_key_for_network_bind("0.0.0.0")
    assert exc_info.value.code == 2
    assert "TRELLIS_API_KEY" in capsys.readouterr().err


def test_startup_allows_network_bind_with_api_key(monkeypatch):
    monkeypatch.setenv("TRELLIS_API_KEY", "test-key")
    server._require_key_for_network_bind("0.0.0.0")  # must not raise


def test_startup_allows_loopback_bind_without_api_key(monkeypatch):
    monkeypatch.delenv("TRELLIS_API_KEY", raising=False)
    server._require_key_for_network_bind("127.0.0.1")  # must not raise


def test_startup_uses_env_host(monkeypatch):
    monkeypatch.setenv("TRELLIS_HOST", "0.0.0.0")
    monkeypatch.delenv("TRELLIS_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        server._require_key_for_network_bind()
    assert exc_info.value.code == 2
