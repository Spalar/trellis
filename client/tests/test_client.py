"""Integration tests for trellis_client against the in-process FastAPI app.

The SDK's httpx.Client is pointed at a SyncASGITransport (see conftest.py)
wrapping the real server app (src/trellis/api.py), so no sockets are involved.
The guard token is computed at import time in src.trellis.core, so tests
monkeypatch ``core.http_token`` directly (same pattern as tests/test_api.py).
"""

from __future__ import annotations

import pytest

from src.trellis.utils import register_project
from trellis_client import TrellisError


def test_health(sdk_client):
    assert sdk_client.health() == {"status": "ok", "version": "0.0.0-test"}


def test_health_needs_no_token(token, make_client, monkeypatch):
    monkeypatch.delenv("TRELLIS_API_KEY", raising=False)
    client = make_client(api_key=None)
    assert client.health()["status"] == "ok"
    client.close()


def test_base_url_trailing_slash_stripped():
    from trellis_client import TrellisClient

    client = TrellisClient(base_url="http://127.0.0.1:17317/")
    assert client.base_url == "http://127.0.0.1:17317"
    client.close()


def test_list_projects_empty_registry(sdk_client):
    assert sdk_client.list_projects() == {"projects": []}


def test_list_projects_registered(tmp_path, sdk_client):
    repo = tmp_path / "repos" / "demo"
    repo.mkdir(parents=True)
    register_project("demo", repo)

    projects = sdk_client.list_projects()["projects"]
    assert len(projects) == 1
    assert projects[0]["id"] == "demo"
    assert projects[0]["path"] == str(repo.resolve())
    assert projects[0]["index_exists"] is False


def test_missing_token_raises_401(token, make_client, monkeypatch):
    monkeypatch.delenv("TRELLIS_API_KEY", raising=False)
    client = make_client(api_key=None)
    with pytest.raises(TrellisError) as exc_info:
        client.list_projects()
    assert exc_info.value.status_code == 401
    assert "Unauthorized" in exc_info.value.message
    client.close()


def test_wrong_token_raises_401(make_client):
    client = make_client(api_key="not-the-token")
    with pytest.raises(TrellisError) as exc_info:
        client.list_projects()
    assert exc_info.value.status_code == 401
    assert "Unauthorized" in exc_info.value.message
    client.close()


def test_api_key_env_fallback(token, make_client, monkeypatch):
    monkeypatch.setenv("TRELLIS_API_KEY", token)
    client = make_client(api_key=None)  # falls back to the env var
    assert client.list_projects() == {"projects": []}
    client.close()


def test_note_round_trip(tmp_path, sdk_client):
    repo = tmp_path / "repos" / "notes-demo"
    repo.mkdir(parents=True)
    register_project("notes-demo", repo)

    created = sdk_client.create_note(
        "notes-demo",
        "hello",
        title="Hello Note",
        content="Some content.",
        tags=["demo"],
    )
    assert created == {"status": "ok", "note_id": "hello", "title": "Hello Note"}

    notes = sdk_client.list_notes("notes-demo")
    assert notes["count"] == 1
    assert notes["notes"][0]["id"] == "hello"

    note = sdk_client.get_note("notes-demo", "hello")
    assert note["title"] == "Hello Note"
    assert note["content"] == "Some content."
    assert note["tags"] == ["demo"]

    deleted = sdk_client.delete_note("notes-demo", "hello")
    assert deleted["status"] == "ok"
    assert sdk_client.list_notes("notes-demo")["count"] == 0

    with pytest.raises(TrellisError) as exc_info:
        sdk_client.get_note("notes-demo", "hello")
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.message


def test_spec_round_trip(tmp_path, sdk_client):
    repo = tmp_path / "repos" / "spec-demo"
    repo.mkdir(parents=True)
    register_project("spec-demo", repo)

    initial = sdk_client.get_spec("spec-demo")
    assert initial["status"] == "no_spec"
    assert initial["project_id"] == "spec-demo"

    saved = sdk_client.save_spec("spec-demo", "# Demo Spec\n")
    assert saved["status"] == "ok"

    fetched = sdk_client.get_spec("spec-demo")
    assert fetched["status"] == "ok"
    assert fetched["content"] == "# Demo Spec\n"


def test_context_manager(make_client):
    with make_client() as client:
        assert client.health()["status"] == "ok"


def test_get_project_detail(tmp_path, sdk_client):
    repo = tmp_path / "repos" / "detail-demo"
    repo.mkdir(parents=True)
    register_project("detail-demo", repo)

    sdk_client.create_note("detail-demo", "n1", title="N1", tags=["adr"])

    detail = sdk_client.get_project("detail-demo")
    assert detail["id"] == "detail-demo"
    assert detail["path"] == str(repo.resolve())
    assert detail["index"]["exists"] is False
    assert detail["spec"]["status"] == "missing"
    assert detail["notes"]["total"] == 1
    assert detail["notes"]["by_tag"] == {"adr": 1}

    with pytest.raises(TrellisError) as exc_info:
        sdk_client.get_project("no-such-project")
    assert exc_info.value.status_code == 404


def test_delete_project(tmp_path, sdk_client):
    repo = tmp_path / "repos" / "del-demo"
    repo.mkdir(parents=True)
    register_project("del-demo", repo)

    removed = sdk_client.delete_project("del-demo")
    assert removed == {"removed": True, "id": "del-demo", "data_deleted": False}
    assert sdk_client.list_projects() == {"projects": []}

    with pytest.raises(TrellisError) as exc_info:
        sdk_client.get_project("del-demo")
    assert exc_info.value.status_code == 404

    with pytest.raises(TrellisError) as exc_info:
        sdk_client.delete_project("del-demo")
    assert exc_info.value.status_code == 404


def test_delete_project_with_delete_data(tmp_path, sdk_client):
    repo = tmp_path / "repos" / "data-del-demo"
    repo.mkdir(parents=True)
    register_project("data-del-demo", repo)
    sdk_client.create_note("data-del-demo", "n1", title="N1", tags=[])

    data_dir = tmp_path / "data" / "projects" / "data-del-demo"
    assert data_dir.exists()

    removed = sdk_client.delete_project("data-del-demo", delete_data=True)
    assert removed["data_deleted"] is True
    assert not data_dir.exists()
    assert repo.exists()


def test_usage_stats(sdk_client):
    stats = sdk_client.usage_stats()
    assert stats["total_calls"] == 0
    assert stats["calls_by_tool"] == {}
    assert stats["last_calls"] == []
