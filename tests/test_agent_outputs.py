"""Agent-facing outputs: complete module maps, compact rows, callees, health filters."""

from __future__ import annotations

import asyncio
import os
import sys
import json
import sqlite3

import pytest

from src.trellis import core
from src.trellis import graph_queries as gq
from src.trellis.bridge import CodeGraphBridge
from src.trellis.python_call_indexer import PythonCallGraphIndexer

from tests.test_python_call_indexer import _impact, _node, project  # noqa: F401


def _call_tool(monkeypatch, bridge, name, **args):
    """Run one MCP tool in-process against a stand-in bridge."""
    from fastmcp import Client

    from src.trellis.mcp_server import create_mcp_server

    monkeypatch.setattr(core, "get_bridge", lambda project_id: bridge)

    async def run():
        async with Client(create_mcp_server("test")) as client:
            result = await client.call_tool(name, {"project_id": "p", **args})
            return json.loads(result.content[0].text)

    return asyncio.run(run())


class FakeBridge:
    def __init__(self, pmap=None, health=None):
        self._pmap = pmap or {}
        self._health = health or {}

    def project_map(self):
        return self._pmap

    def project_health(self, limit):
        return self._health


PMAP = {
    "modules": [
        {"path": "src/a", "files": 2, "functions": 5, "classes": 1, "languages": ["python"]},
        {"path": "src/b", "files": 1, "functions": 2, "languages": ["python", "markdown"]},
    ],
    "module_dependencies": [
        {"from": "src/b", "to": "src/a", "imports": 3},
        {"from": "src/a", "to": "src/c", "imports": 7},
    ],
}


def test_list_modules_compact_lines(monkeypatch):
    result = _call_tool(monkeypatch, FakeBridge(PMAP), "trellis_list_modules")
    assert result == {
        "module_count": 2,
        "modules": [
            "src/a: 2 files, 6 symbols (python)",
            "src/b: 1 files, 2 symbols (python, markdown)",
        ],
    }


def test_boundary_map_reads_engine_module_dependencies(monkeypatch):
    result = _call_tool(monkeypatch, FakeBridge(PMAP), "trellis_get_boundary_map")
    assert result["dependencies"] == ["src/a -> src/c (7 imports)", "src/b -> src/a (3 imports)"]
    assert result["dependency_count"] == 2 and not result["dependencies_truncated"]


def test_project_health_drops_test_code_and_compacts(monkeypatch):
    health = {
        "chokepoints": [
            {"file_path": "app/tests.py", "name": "save", "type": "method", "normalized": 0.5, "caller_count": 200},
            {"file_path": "app/flow.py", "name": "apply", "type": "function", "normalized": 0.0183661, "caller_count": 22},
        ],
        "import_cycles": [{"cycle": ["a.py", "b.py", "a.py"], "files": ["a.py", "b.py"], "size": 2}],
        "surprising_connections": [
            {"source_file": "x.py", "source": "f", "target_file": "app/tests.py", "target": "save",
             "relation": "calls", "confidence": "ambiguous", "why": ["w"]},
        ],
    }
    result = _call_tool(monkeypatch, FakeBridge(health=health), "trellis_project_health", limit=5)
    assert result["chokepoints"] == ["app/flow.py: apply (function, betweenness 0.0184, 22 callers)"]
    assert result["import_cycles"] == [{"loop": "a.py -> b.py -> a.py", "size": 2, "files_sample": ["a.py", "b.py"]}]
    assert result["surprising_connections"] == []


def _cli_bridge(monkeypatch, cli_result, mcp_result=None):
    bridge = CodeGraphBridge.__new__(CodeGraphBridge)
    monkeypatch.setattr(bridge, "_run_cli", lambda *a, **k: cli_result, raising=False)
    monkeypatch.setattr(bridge, "_call", lambda *a, **k: mcp_result or {}, raising=False)
    return bridge


def test_project_map_prefers_untruncated_cli_json(monkeypatch):
    payload = {"modules": [{"path": f"m{i}"} for i in range(40)], "module_dependencies": []}
    bridge = _cli_bridge(monkeypatch, {"success": True, "stdout": json.dumps(payload)},
                         mcp_result={"modules": [{"path": "m0"}], "_truncated": True})
    assert len(bridge.project_map()["modules"]) == 40


def test_project_map_falls_back_to_mcp(monkeypatch):
    bridge = _cli_bridge(monkeypatch, {"success": False, "stdout": "", "stderr": "boom"},
                         mcp_result={"modules": [{"path": "m0"}]})
    assert bridge.project_map()["modules"] == [{"path": "m0"}]


def test_index_being_built_is_an_error_not_an_empty_answer(monkeypatch):
    building = {
        "success": False,
        "stdout": "The index is still being built (10/200 files); answers from it would be incomplete.",
        "stderr": "",
    }
    bridge = _cli_bridge(monkeypatch, building, mcp_result={"modules": []})
    with pytest.raises(RuntimeError, match="still being built"):
        bridge.project_map()
    with pytest.raises(RuntimeError, match="still being built"):
        bridge.tour()


def test_symbol_line_forms():
    assert core.symbol_line(
        {"file_path": "a.py", "start_line": 3, "end_line": 9, "type": "function", "name": "f",
         "signature": "(x,\n      y)"}
    ) == "a.py:3-9 function f(x, y)"
    assert core.symbol_line(
        {"file_path": "a.ts", "line": 4, "qualified_name": "C.m", "type": "method", "return_type": "Promise"}
    ) == "a.ts:4 method C.m -> Promise"


def test_affected_functions_are_callers_not_same_file_functions(project):  # noqa: F811
    root, _ = project
    PythonCallGraphIndexer(str(root)).index_calls()

    report = _impact(root, "services/billing.py:charge")
    assert [gq.describe(c) for c in report["callers"]] == ["backend/views.py:view"]
    assert report["affected_functions_count"] == 1
    assert [f["qualified_name"] for f in report["same_file_functions"]] == ["lookup"]


def test_callees_skip_test_targets_of_production_callers(project):  # noqa: F811
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()
    conn = sqlite3.connect(str(db))
    view, test_fn = _node(conn, "backend/views.py:view"), _node(conn, "tests/test_views.py:test_it")
    # A mis-resolved method call from production code into a test file.
    conn.execute(
        "INSERT INTO edges (source_id, target_id, relation, confidence) VALUES (?, ?, 'calls', 'inferred')",
        (view, test_fn),
    )
    conn.commit()
    names = {c["qualified_name"] for c in gq.callees_of(conn, view)}
    conn.close()
    assert "test_it" not in names
    assert {"charge", "lookup", "exported", "local"} <= names


def test_lookup_symbol_adds_callers_and_callees(project):  # noqa: F811
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()
    bridge = CodeGraphBridge.__new__(CodeGraphBridge)
    bridge.code_graph_path = db.parent

    node = bridge.lookup_symbol("services/billing.py:charge")
    assert [gq.describe(c) for c in node["callers"]] == ["backend/views.py:view"]
    assert node["test_caller_count"] == 1
    assert node["callees"] == []
    assert {c["qualified_name"] for c in bridge.lookup_symbol("backend/views.py:view")["callees"]} >= {"charge"}


def test_code_graph_file_gets_a_clear_error(tmp_path):
    from src.trellis.utils import get_code_graph_path

    (tmp_path / ".code-graph").write_text("K:/old/data/projects/x/.code-graph")
    with pytest.raises(RuntimeError, match="is a file, not the index directory"):
        get_code_graph_path(tmp_path)


def test_named_caller_count_skips_tests_and_guesses(project):  # noqa: F811
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()
    conn = sqlite3.connect(str(db))
    view = _node(conn, "backend/views.py:view")
    conn.execute(
        "INSERT INTO edges (source_id, target_id, relation, confidence) "
        "SELECT ?, id, 'calls', 'ambiguous' FROM nodes WHERE name = 'lookup'",
        (view,),
    )
    conn.commit()
    assert gq.named_caller_count(conn, "backend/services/billing.py", "charge", "function") == 1
    conn.close()


def test_ast_search_widens_pool_and_puts_exact_name_first(monkeypatch):
    sent = {}

    def fake_call(tool, **args):
        sent.update(args)
        return {"results": [{"name": "follow_auto_chain"}, {"name": "Flow.apply_transition"}, {"name": "x"}]}

    bridge = _cli_bridge(monkeypatch, {})
    monkeypatch.setattr(bridge, "_call", fake_call, raising=False)
    results = bridge.ast_search(query="apply_transition", node_type="function", limit=2)
    assert sent["limit"] >= 30 and sent["type"] == "function" and "node_type" not in sent
    assert [r["name"] for r in results] == ["Flow.apply_transition", "follow_auto_chain"]


def test_call_graph_rollups_become_nodes():
    rollup = {"rollups": [{"file": "a.py", "names": ["f", "g"], "node_ids": [1, 2], "min_depth": 1}], "total_count": 2}
    nodes = CodeGraphBridge._call_graph_nodes(rollup, "callers")
    assert [(n["name"], n["node_id"], n["location"]["file_path"], n["direction"]) for n in nodes] == [
        ("f", 1, "a.py", "callers"),
        ("g", 2, "a.py", "callers"),
    ]
    listed = CodeGraphBridge._call_graph_nodes([{"name": "h", "file_path": "b.py", "start_line": 3}], "callees")
    assert listed[0]["location"] == {"file_path": "b.py", "start_line": 3} and listed[0]["direction"] == "callees"


def test_mcp_subcommand_defaults_to_stdio_in_bundle(monkeypatch):
    """`trellis mcp` is stdio even though the bundle's launcher defaults to HTTP."""
    import server

    def run_mcp():
        os.environ.setdefault("TRELLIS_TRANSPORT", "http")  # launcher.setup_environment
        seen.append(os.environ["TRELLIS_TRANSPORT"])

    seen = []
    monkeypatch.setattr(server, "_run_mcp", run_mcp)
    for argv, want in ((["trellis", "mcp"], "stdio"), (["trellis"], "http")):
        monkeypatch.delenv("TRELLIS_TRANSPORT", raising=False)
        monkeypatch.setattr(sys, "argv", argv)
        server.main()
        assert seen[-1] == want
