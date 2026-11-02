"""Tests for the CLI stdout parsers used by the code-graph bridge."""

from __future__ import annotations

import json

from src.trellis.cli_parsers import parse_json_array, parse_tour


def test_parse_tour_envelope():
    stdout = json.dumps(
        {
            "reading_order": [
                {
                    "path": "src/util",
                    "role": "foundational",
                    "depended_on_by": 3,
                    "depends_on": [],
                    "key_symbols": ["helper"],
                    "in_cycle": False,
                },
                {
                    "path": "src/main",
                    "role": "entry",
                    "depended_on_by": 0,
                    "depends_on": ["src/util"],
                    "key_symbols": ["main"],
                    "in_cycle": True,
                },
            ]
        }
    )
    result = parse_tour(stdout)
    order = result["reading_order"]
    assert len(order) == 2
    assert order[0]["path"] == "src/util"
    assert order[1]["in_cycle"] is True


def test_parse_tour_garbage_returns_empty_order():
    assert parse_tour("not json at all") == {"reading_order": []}
    assert parse_tour('["an", "array"]') == {"reading_order": []}
    assert parse_tour('{"other_key": 1}') == {"reading_order": []}
    assert parse_tour('{"reading_order": "notalist"}') == {"reading_order": []}


def test_parse_json_array_centrality_shape():
    stdout = json.dumps(
        [
            {
                "betweenness": 285.0,
                "caller_count": 6,
                "file_path": "src/bridge.py",
                "name": "_call",
                "normalized": 0.0006,
                "type": "method",
            }
        ]
    )
    result = parse_json_array(stdout)
    assert len(result) == 1
    assert result[0]["name"] == "_call"
    assert result[0]["betweenness"] == 285.0


def test_parse_json_array_cycles_shape():
    stdout = json.dumps(
        [{"files": ["a.py", "b.py"], "size": 2, "cycle": ["a.py", "b.py", "a.py"]}]
    )
    result = parse_json_array(stdout)
    assert result[0]["size"] == 2
    assert result[0]["cycle"] == ["a.py", "b.py", "a.py"]


def test_parse_json_array_empty_is_success():
    assert parse_json_array("[]") == []


def test_parse_json_array_garbage_returns_empty_list():
    assert parse_json_array("") == []
    assert parse_json_array("error: something broke") == []
    assert parse_json_array('{"envelope": true}') == []
    assert parse_json_array('["not", "dicts", 1]') == []


def test_parse_impact_current_format_and_nesting():
    from src.trellis.cli_parsers import parse_impact

    out = (
        "Impact: addIcon — Risk: MEDIUM\n"
        "  2 direct, 4 callers total, 3 files, 0 routes (0 tests affected)\n"
        "Callers:\n"
        "  _iconAction  (method) src/action.js\n"
        "  _addIconHandler  (method) src/ui/icon.js\n"
        "    getActions  (method) src/action.js\n"
        "      constructor  (method) src/imageEditor.js\n"
    )
    for text in (out, out.replace("—", "�")):  # dash lost to the console codepage
        result = parse_impact(text)
        assert result["risk"] == "MEDIUM"
        assert (result["direct_callers"], result["total_callers"], result["files"]) == (2, 4, 3)
        handler = result["callers"][1]
        assert handler["children"][0]["name"] == "getActions"
        assert handler["children"][0]["children"][0]["name"] == "constructor"
