"""Tests for import-aware Python call resolution and caller lookup."""

from __future__ import annotations

import ast
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.trellis import graph_queries as gq
from src.trellis.python_call_indexer import PythonCallGraphIndexer
from src.trellis.utils import get_code_graph_path

SOURCES = {
    "backend/services/__init__.py": "",
    "backend/services/billing.py": """
def charge(item):
    return item

def lookup(user):
    return user
""",
    "backend/services/shipping.py": """
def charge(item):
    return item
""",
    "backend/services/pricing.py": """
def lookup(user):
    return user
""",
    "backend/services/base.py": """
class Base:
    def save(self):
        return 1
""",
    "backend/services/models.py": """
from services.base import Base

class Order(Base):
    def total(self):
        return self.save()

    def close(self):
        return self.total()

    @classmethod
    def make(cls):
        return cls.total()
""",
    "backend/pkg/__init__.py": "from pkg.impl import exported\n",
    "backend/pkg/impl.py": """
def exported():
    return 1
""",
    "backend/pkg/rel.py": """
from .impl import exported

def use():
    return exported()
""",
    "backend/views.py": """
from services.billing import charge
from services import pricing as prices
import services.shipping
from pkg import exported
from services.models import Order

def decorator(f):
    return f

@decorator
def view(cart, obj):
    charge(1)
    prices.lookup(2)
    services.shipping.charge(3)
    cart.lookup(4)
    obj.save()
    exported()
    Order.make()
    return local()

def local():
    def inner():
        return 1
    return inner()
""",
    "backend/closures.py": """
def b():
    return 0

def sort_by(items, b):
    return b(items)  # the parameter, not module-level b

def shadow():
    b = len
    return b([])

def outer():
    def a():
        return b()

    def b():
        return 1

    return a()
""",
    "backend/tests/test_views.py": """
from services.billing import charge

def test_it():
    charge(1)
""",
}


def _def_lines(source: str):
    """(qualified name, start line, end line, type) like code-graph records them."""
    out = []

    def walk(body, prefix, in_class):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
                kind = "method" if in_class else "function"
                out.append((prefix + node.name, node.name, start, node.end_lineno, kind))
                walk(node.body, "", False)
            elif isinstance(node, ast.ClassDef):
                out.append((node.name, node.name, node.lineno, node.end_lineno, "class"))
                walk(node.body, node.name + ".", True)

    walk(ast.parse(source).body, "", False)
    return out


@pytest.fixture
def project(monkeypatch):
    data_dir = tempfile.mkdtemp(prefix="trellis-data-")
    project_dir = tempfile.mkdtemp(prefix="trellis-project-")
    monkeypatch.setenv("TRELLIS_DATA_DIR", data_dir)
    root = Path(project_dir)
    for rel, text in SOURCES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")

    cg = get_code_graph_path(root)
    (cg / "source-roots.json").write_text(json.dumps({"roots": ["backend"]}))
    conn = sqlite3.connect(str(cg / "index.db"))
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT UNIQUE, indexed_at INTEGER);
        CREATE TABLE nodes (id INTEGER PRIMARY KEY, file_id INTEGER, type TEXT, name TEXT,
            qualified_name TEXT, start_line INTEGER, end_line INTEGER, code_content TEXT,
            signature TEXT);
        CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER,
            relation TEXT, metadata TEXT, confidence TEXT NOT NULL DEFAULT 'extracted');
        CREATE UNIQUE INDEX idx_edges_unique
            ON edges(source_id, target_id, relation, COALESCE(metadata, ''));
        """
    )
    for fid, (rel, text) in enumerate(SOURCES.items(), start=1):
        conn.execute("INSERT INTO files VALUES (?, ?, 1)", (fid, rel))
        for qname, name, start, end, kind in _def_lines(text):
            conn.execute(
                "INSERT INTO nodes (file_id, type, name, qualified_name, start_line,"
                " end_line, code_content) VALUES (?, ?, ?, ?, ?, ?, '')",
                (fid, kind, name, qname, start, end),
            )
    conn.commit()
    conn.close()

    yield root, cg / "index.db"

    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(project_dir, ignore_errors=True)


def _node(conn, spec):
    return gq.find_symbol(conn, spec)[0]["id"]


def _callers(db_path, spec):
    with gq.connect(db_path) as conn:
        found = gq.callers_of(conn, _node(conn, spec))
    return {k: sorted(gq.describe(c) for c in v) for k, v in found.items()}


def test_calls_resolve_through_imports(project):
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()

    assert _callers(db, "services/billing.py:charge")["callers"] == ["backend/views.py:view"]
    assert _callers(db, "services/shipping.py:charge")["callers"] == ["backend/views.py:view"]
    assert _callers(db, "pricing.py:lookup")["callers"] == ["backend/views.py:view"]
    # cart.lookup(): unknown object, so billing.lookup gets no guessed caller
    assert _callers(db, "services/billing.py:lookup")["callers"] == []


def test_reexport_relative_import_and_nested_def(project):
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()

    assert _callers(db, "pkg/impl.py:exported")["callers"] == [
        "backend/pkg/rel.py:use",
        "backend/views.py:view",
    ]
    assert _callers(db, "views.py:local")["callers"] == ["backend/views.py:view"]
    assert _callers(db, "views.py:inner")["callers"] == ["backend/views.py:local"]


def test_self_cls_and_inherited_methods(project):
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()

    assert _callers(db, "Base.save")["callers"] == ["backend/services/models.py:Order.total"]
    assert _callers(db, "Order.total")["callers"] == [
        "backend/services/models.py:Order.close",
        "backend/services/models.py:Order.make",
    ]
    assert _callers(db, "Order.make")["callers"] == ["backend/views.py:view"]


def test_test_callers_are_split_out(project):
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()

    found = _callers(db, "services/billing.py:charge")
    assert found["test_callers"] == ["backend/tests/test_views.py:test_it"]


def test_sync_removes_stale_and_skips_upstream_pairs(project):
    root, db = project
    conn = sqlite3.connect(str(db))
    view = _node(conn, "views.py:view")
    billing_lookup = _node(conn, "services/billing.py:lookup")
    billing_charge = _node(conn, "services/billing.py:charge")
    pricing_lookup = _node(conn, "pricing.py:lookup")
    conn.executemany(
        "INSERT INTO edges (source_id, target_id, relation, metadata, confidence)"
        " VALUES (?, ?, 'calls', ?, ?)",
        [
            (view, billing_lookup, '{"caller": "view"}', "extracted"),  # old wrong guess
            (view, billing_charge, None, "extracted"),  # upstream already right
            (view, pricing_lookup, '{"q":"module"}', "ambiguous"),  # upstream unsure
        ],
    )
    conn.commit()
    conn.close()

    PythonCallGraphIndexer(str(root)).index_calls()

    conn = sqlite3.connect(str(db))
    owned = set(
        conn.execute(
            "SELECT source_id, target_id FROM edges WHERE metadata LIKE '{\"caller\"%'"
        )
    )
    conn.close()
    assert (view, billing_lookup) not in owned
    assert (view, billing_charge) not in owned
    assert (view, pricing_lookup) in owned


def test_rerun_is_idempotent_and_reparses_only_edits(project):
    root, db = project
    indexer = PythonCallGraphIndexer(str(root))
    first = indexer.index_calls()
    second = indexer.index_calls()

    assert first["call_edges_added"] > 0
    assert second["files_parsed"] == 0
    assert second["call_edges_added"] == second["call_edges_removed"] == 0


def test_find_symbol_forms(project):
    _, db = project
    with gq.connect(db) as conn:
        assert gq.describe(gq.find_symbol(conn, "backend/services/billing.py:charge")[0]) == (
            "backend/services/billing.py:charge"
        )
        assert gq.describe(gq.find_symbol(conn, "services.shipping.charge")[0]) == (
            "backend/services/shipping.py:charge"
        )
        assert gq.describe(gq.find_symbol(conn, "services.models.Order.total")[0]) == (
            "backend/services/models.py:Order.total"
        )
        assert len(gq.find_symbol(conn, "charge")) == 2
        assert gq.find_symbol(conn, "nope.py:charge") == []


def test_split_symbol():
    assert gq.split_symbol("a/b.py:C.m") == ("a/b.py", "C.m")
    assert gq.split_symbol("a\\b.py::f") == ("a/b.py", "f")
    assert gq.split_symbol("f") == ("", "f")


def _impact(root, symbol, depth=1):
    from src.trellis.impact_analyzer import ImpactAnalyzer

    return ImpactAnalyzer(None, str(root)).analyze_impact(symbol, depth=depth)


def test_class_impact_counts_method_callers(project):
    root, _ = project
    PythonCallGraphIndexer(str(root)).index_calls()

    report = _impact(root, "services/models.py:Order")
    assert [gq.describe(c) for c in report["callers"]] == ["backend/views.py:view"]


def test_deep_impact_lists_indirect_callers(project):
    root, _ = project
    PythonCallGraphIndexer(str(root)).index_calls()

    report = _impact(root, "Base.save", depth=3)
    assert [gq.describe(c) for c in report["callers"]] == ["backend/services/models.py:Order.total"]
    indirect = {(c["depth"], c["qualified_name"]) for c in report["indirect_callers"]}
    assert indirect == {(2, "Order.close"), (2, "Order.make"), (3, "view")}
    assert _impact(root, "Base.save")["indirect_callers"] == []


def test_only_ambiguous_callers_do_not_mean_low_risk(project):
    root, db = project
    conn = sqlite3.connect(str(db))
    target = _node(conn, "services/billing.py:lookup")
    sources = [
        row[0]
        for row in conn.execute("SELECT id FROM nodes WHERE type IN ('function','method')")
        if row[0] != target
    ]
    conn.executemany(
        "INSERT INTO edges (source_id, target_id, relation, metadata, confidence)"
        " VALUES (?, ?, 'calls', NULL, 'ambiguous')",
        [(s, target) for s in sources],
    )
    conn.commit()
    conn.close()

    report = _impact(root, "services/billing.py:lookup")
    assert report["caller_count"] == 0
    assert report["risk_level"] != "LOW" and report["risk_note"]


def test_trace_path_between_modules(project):
    from src.trellis.mcp_server import _trace_path

    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()

    class FakeBridge:
        project_path = root

    result = _trace_path(FakeBridge(), db, "backend/VIEWS.py", "services/billing.py")
    assert {
        tuple(side.split(":")[0] for side in e.split(" (")[0].split(" -> "))
        for e in result["direct_connections"]
    } == {("backend/views.py", "backend/services/billing.py")}
    assert "error" in _trace_path(FakeBridge(), db, "nowhere", "services/billing.py")


def test_nested_def_called_before_its_definition(project):
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()

    with gq.connect(db) as conn:
        rows = {
            line: nid
            for nid, line in conn.execute(
                "SELECT n.id, n.start_line FROM nodes n JOIN files f ON f.id = n.file_id"
                " WHERE f.path = 'backend/closures.py' AND n.name = 'b'"
            )
        }
        top, nested = rows[min(rows)], rows[max(rows)]
        assert gq.callers_of(conn, top)["callers"] == []
        assert [c["qualified_name"] for c in gq.callers_of(conn, nested)["callers"]] == ["a"]


def test_parameters_and_locals_shadow_module_functions(project):
    root, db = project
    PythonCallGraphIndexer(str(root)).index_calls()

    with gq.connect(db) as conn:
        top = conn.execute(
            "SELECT n.id FROM nodes n JOIN files f ON f.id = n.file_id"
            " WHERE f.path = 'backend/closures.py' AND n.name = 'b' ORDER BY n.start_line"
        ).fetchone()[0]
        assert gq.callers_of(conn, top)["callers"] == []
