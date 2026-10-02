"""Tests for MCP tool-call telemetry (src/trellis/usage.py)."""

from __future__ import annotations

import json

import pytest

from src.trellis import usage


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """Isolated trellis data dir for usage.json."""
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def test_record_call_accumulates(data_dir):
    usage.record_call("trellis_sync", "demo", True, 12.5)
    usage.record_call("trellis_sync", "demo", False, 3.0)
    usage.record_call("trellis_list_modules", "demo", True, 1.0)

    doc = usage.get_usage()
    assert doc["total_calls"] == 3
    assert doc["calls_by_tool"] == {"trellis_sync": 2, "trellis_list_modules": 1}
    assert doc["errors"] == {"trellis_sync": 1}
    assert doc["first_call_at"] is not None
    assert doc["last_call_at"] is not None

    entry = doc["last_calls"][-1]
    assert entry["tool"] == "trellis_list_modules"
    assert entry["project_id"] == "demo"
    assert entry["ok"] is True
    assert entry["duration_ms"] == 1.0
    assert "at" in entry


def test_ring_trims_to_50_newest_last(data_dir):
    for i in range(60):
        usage.record_call(f"tool_{i}", "demo", True, 0.1)

    doc = usage.get_usage()
    last_calls = doc["last_calls"]
    assert len(last_calls) == 50
    assert last_calls[0]["tool"] == "tool_10"
    assert last_calls[-1]["tool"] == "tool_59"
    assert doc["total_calls"] == 60
    # Per-tool counts keep everything ever recorded, not just the ring.
    assert doc["calls_by_tool"]["tool_0"] == 1
    assert doc["calls_by_tool"]["tool_59"] == 1


def test_get_usage_defaults_on_missing_file(data_dir):
    doc = usage.get_usage()
    assert doc == {
        "total_calls": 0,
        "calls_by_tool": {},
        "errors": {},
        "first_call_at": None,
        "last_call_at": None,
        "last_calls": [],
    }


def test_corrupt_file_resets_and_records(data_dir):
    data_dir.mkdir(parents=True)
    (data_dir / "usage.json").write_text("not json {{{", encoding="utf-8")

    usage.record_call("trellis_sync", "demo", True, 1.0)
    doc = usage.get_usage()
    assert doc["total_calls"] == 1
    assert doc["calls_by_tool"] == {"trellis_sync": 1}


def test_reset_usage(data_dir):
    usage.record_call("trellis_sync", "demo", True, 1.0)
    doc = usage.reset_usage()
    assert doc["total_calls"] == 0
    assert usage.get_usage()["total_calls"] == 0


def test_persists_across_restart(data_dir):
    """New reads see what a previous 'process' persisted (file is the state)."""
    usage.record_call("trellis_sync", "demo", True, 1.0)
    # Simulate a restart: drop any module state (usage.py keeps none) and
    # re-read purely from disk.
    raw = json.loads((data_dir / "usage.json").read_text(encoding="utf-8"))
    assert raw["total_calls"] == 1
    doc = usage.get_usage()
    assert doc["calls_by_tool"] == {"trellis_sync": 1}
    assert doc["last_calls"][0]["tool"] == "trellis_sync"


def test_mcp_tool_call_recorded_through_server(data_dir):
    """A real in-process FastMCP call passes through the recording middleware."""
    import anyio

    from src.trellis.mcp_server import create_mcp_server

    usage.reset_usage()
    mcp = create_mcp_server("0.0.0-test")

    from fastmcp import Client

    async def _call():
        async with Client(mcp) as client:
            return await client.call_tool("trellis_sync_status", {"project_id": ""})

    anyio.run(_call)

    doc = usage.get_usage()
    assert doc["total_calls"] == 1
    entry = doc["last_calls"][-1]
    assert entry["tool"] == "trellis_sync_status"
    assert entry["ok"] is True
    assert entry["duration_ms"] >= 0
