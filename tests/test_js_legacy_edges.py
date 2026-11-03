"""Cleanup of JS edges written by augmentor versions that did not mark them."""

from __future__ import annotations

import sqlite3

from src.trellis.js_graph_augmentor import OWNED_META, JSGraphAugmentor


def test_only_unmarked_cross_file_js_leftovers_are_removed():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE nodes (id INTEGER PRIMARY KEY, file_id INTEGER);
        CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER,
            relation TEXT, metadata TEXT, confidence TEXT NOT NULL DEFAULT 'extracted');
        INSERT INTO files VALUES (1, 'a.js'), (2, 'b.js'), (3, 'c.py'), (4, 'd.py');
        INSERT INTO nodes VALUES (1, 1), (2, 1), (3, 2), (4, 3), (5, 4);
        """
    )
    rows = {
        "legacy": (1, 3, "calls", None, "extracted"),  # cross-file, unmarked, default
        "same_file": (1, 2, "calls", None, "extracted"),  # this.method() in its own file
        "upstream_guess": (2, 3, "calls", None, "inferred"),
        "upstream_import": (1, 3, "calls", '{"q":"imp"}', "extracted"),
        "python": (4, 5, "calls", None, "extracted"),
        "owned": (2, 3, "calls", OWNED_META, "extracted"),
        "import_edge": (1, 3, "imports", None, "extracted"),
    }
    ids = {}
    for name, row in rows.items():
        ids[name] = conn.execute(
            "INSERT INTO edges (source_id, target_id, relation, metadata, confidence)"
            " VALUES (?, ?, ?, ?, ?)",
            row,
        ).lastrowid

    assert JSGraphAugmentor._remove_legacy_edges(conn) == 1
    left = {row[0] for row in conn.execute("SELECT id FROM edges")}
    assert left == set(ids.values()) - {ids["legacy"]}


def test_older_index_without_confidence_is_left_alone():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY, relation TEXT, metadata TEXT)")
    assert JSGraphAugmentor._remove_legacy_edges(conn) == 0


def test_import_specifier_matching():
    from src.trellis.js_graph_augmentor import _specifier_matches

    caller = "src/js/ui.js"
    assert _specifier_matches("./ui/shape", caller, "src/js/ui/shape.js")
    assert _specifier_matches("../js/ui/shape.js", caller, "src/js/ui/shape.js")
    assert _specifier_matches("./ui", caller, "src/js/ui/index.js")
    assert _specifier_matches("@/ui/shape", caller, "src/js/ui/shape.js")
    assert not _specifier_matches("@/ui/shape", caller, "src/js/component/shape.js")
    assert not _specifier_matches("./ui/shape", caller, "lib/ui/shape.js")
