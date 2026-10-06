"""Tests for project detail/removal endpoints and the usage stats endpoint."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from src.trellis import core
from src.trellis.api import create_api_app
from src.trellis.utils import register_project

LOCAL_HOST = {"Host": "127.0.0.1"}


def _auth_headers():
    return {"Host": "127.0.0.1", "X-Trellis-Token": core.http_token}


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """API app against an isolated trellis data dir."""
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    return TestClient(create_api_app("0.0.0-test"))


@pytest.fixture
def registered_repo(tmp_path):
    repo = tmp_path / "repos" / "demo"
    repo.mkdir(parents=True)
    register_project("demo", repo)
    return repo


def test_project_detail_shape(api_client, registered_repo):
    from src.trellis.knowledge_graph import NoteGraph

    graph = NoteGraph(str(registered_repo))
    graph.save_note("arch", "content", title="Arch", tags=["feature", "adr"])
    graph.save_note("notes", "content", title="Notes", tags=["feature"])

    resp = api_client.get("/projects/demo", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "demo"
    assert body["name"] == "demo"
    assert body["path"] == str(registered_repo.resolve())
    assert body["index"] == {
        "exists": False,
        "nodes": None,
        "files": None,
        "modules": None,
    }
    assert body["sync"] is None
    assert body["spec"]["status"] == "missing"
    assert body["notes"]["total"] == 2
    assert body["notes"]["by_tag"] == {"feature": 2, "adr": 1}


def test_project_detail_notes_never_fail(api_client, registered_repo, monkeypatch):
    """A notes-load failure degrades to total 0 rather than erroring."""
    from src.trellis import knowledge_graph

    def _boom(*args, **kwargs):
        raise RuntimeError("notes exploded")

    monkeypatch.setattr(knowledge_graph, "NoteGraph", _boom)

    resp = api_client.get("/projects/demo", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["notes"] == {"total": 0, "by_tag": {}}


def test_project_search_endpoint(api_client, monkeypatch):
    """GET /projects/{id}/search is the REST equivalent of trellis_search_code."""
    fake_bridge = SimpleNamespace(search=lambda q, limit=10: [{"name": q, "limit": limit}])
    monkeypatch.setattr(core, "get_bridge", lambda pid: fake_bridge)

    resp = api_client.get(
        "/projects/app/search?q=checkout&limit=5", headers=_auth_headers()
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == [{"name": "checkout", "limit": 5}]


def test_project_detail_unknown_is_404(api_client):
    resp = api_client.get("/projects/nope", headers=_auth_headers())
    assert resp.status_code == 404
    assert "error" in resp.json()


def test_delete_project_removes_from_registry(api_client, registered_repo):
    resp = api_client.delete("/projects/demo", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"removed": True, "id": "demo", "data_deleted": False}

    resp = api_client.get("/projects/demo", headers=_auth_headers())
    assert resp.status_code == 404
    resp = api_client.get("/projects", headers=_auth_headers())
    assert resp.json() == {"projects": []}


def test_delete_project_evicts_cached_bridge(api_client, registered_repo, monkeypatch):
    bridge = object()
    monkeypatch.setitem(core._bridge_cache, "demo", bridge)
    closed = []
    monkeypatch.setattr(
        core, "evict_bridge", lambda pid: closed.append(pid) or core._bridge_cache.pop(pid, None)
    )
    resp = api_client.delete("/projects/demo", headers=_auth_headers())
    assert resp.status_code == 200
    assert closed == ["demo"]


def test_delete_project_with_delete_data_removes_data_dir(api_client, tmp_path):
    repo = tmp_path / "repos" / "data-demo"
    repo.mkdir(parents=True)
    register_project("data-demo", repo)
    data_dir = tmp_path / "data" / "projects" / "data-demo"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "note.md").write_text("note", encoding="utf-8")

    resp = api_client.delete(
        "/projects/data-demo?delete_data=true", headers=_auth_headers()
    )
    assert resp.status_code == 200
    assert resp.json()["data_deleted"] is True
    assert not data_dir.exists()
    # The repository itself is never touched.
    assert repo.exists()


def test_delete_project_unknown_is_404(api_client):
    resp = api_client.delete("/projects/nope", headers=_auth_headers())
    assert resp.status_code == 404
    assert "error" in resp.json()


def test_delete_project_rejects_pathlike_id(api_client):
    # URL-supplied ids are validated before use as registry keys/paths.
    # (an encoded slash never reaches the route — it 404s at the router,
    # which is safe too — so exercise a non-slash invalid character)
    resp = api_client.delete("/projects/bad:id?delete_data=true", headers=_auth_headers())
    assert resp.status_code == 400


def test_stats_usage_defaults(api_client):
    resp = api_client.get("/stats/usage", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_calls"] == 0
    assert body["calls_by_tool"] == {}
    assert body["errors"] == {}
    assert body["first_call_at"] is None
    assert body["last_call_at"] is None
    assert body["last_calls"] == []


def test_stats_usage_requires_token(api_client):
    resp = api_client.get("/stats/usage", headers=LOCAL_HOST)
    assert resp.status_code == 401
