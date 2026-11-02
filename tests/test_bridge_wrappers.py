"""Bridge wrappers keep their documented shapes across engine output changes."""

from __future__ import annotations

from src.trellis.bridge import CodeGraphBridge


def _bridge(monkeypatch, payload):
    bridge = CodeGraphBridge.__new__(CodeGraphBridge)
    monkeypatch.setattr(bridge, "_mcp_then_cli", lambda *a, **k: payload, raising=False)
    return bridge


def test_find_references_dict_becomes_list(monkeypatch):
    payload = {
        "references": [
            {"name": "constructor", "file_path": "src/app.js", "start_line": 9, "relation": "calls"}
        ],
        "total_references": 1,
    }
    refs = _bridge(monkeypatch, payload).find_references("Graphics")
    assert refs == [
        {
            "name": "constructor",
            "file_path": "src/app.js",
            "start_line": 9,
            "relation": "calls",
            "location": {"file_path": "src/app.js", "start_line": 9},
        }
    ]


def test_call_graph_gets_symbol_and_nodes(monkeypatch):
    payload = {
        "function": "addIcon",
        "callers": [{"name": "_iconAction", "file_path": "src/action.js", "type": "method", "depth": 1}],
        "callees": [],
    }
    result = _bridge(monkeypatch, payload).get_call_graph("addIcon", direction="callers")
    assert result["symbol"] == "addIcon"
    assert result["nodes"][0]["name"] == "_iconAction"
    assert result["nodes"][0]["location"]["file_path"] == "src/action.js"


def test_module_overview_groups_exports_by_file(monkeypatch):
    payload = {
        "active_exports": [
            {"file": "src/a.js", "name": "f", "type": "function"},
            {"file": "src/a.js", "name": "C", "type": "class"},
            {"file": "src/b.js", "name": "g", "type": "function"},
        ]
    }
    result = _bridge(monkeypatch, payload).module_overview("src")
    assert result["files"] == {
        "src/a.js": [{"kind": "function", "names": ["f"]}, {"kind": "class", "names": ["C"]}],
        "src/b.js": [{"kind": "function", "names": ["g"]}],
    }


def test_impact_graph_is_built_from_the_impact_report(monkeypatch):
    bridge = CodeGraphBridge.__new__(CodeGraphBridge)
    report = {
        "resolved": "src/a.py:f",
        "risk_level": "MEDIUM",
        "callers": [{"name": "g", "qualified_name": "g", "file_path": "src/b.py", "line": 3}],
        "same_file_functions": [{"name": "h", "qualified_name": "h", "file_path": "src/a.py", "line": 9}],
    }
    monkeypatch.setattr(bridge, "get_feature_impact", lambda symbol, depth=1: report, raising=False)
    graph = bridge.get_impact_graph("f")
    assert graph["risk_level"] == "MEDIUM"
    assert {n["impact_type"] for n in graph["nodes"][1:]} == {"caller", "same_file"}
    assert len(graph["links"]) == 2
