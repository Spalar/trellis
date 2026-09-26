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
