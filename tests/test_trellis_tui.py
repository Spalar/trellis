"""Integration tests validating Trellis against the TUI image-editor fixture.

These tests verify that dynamic-dispatch JS patterns are now captured:
- commandFactory.register / invoker.execute
- new ClassName() instantiation
- graphics.getComponent(componentNames.X)
- this._prop.method() typed property calls
- this._methodName() intra-class calls
"""

import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.trellis.bridge import CodeGraphBridge
from src.trellis.js_graph_augmentor import JSGraphAugmentor
from src.trellis.js_pattern_indexer import JsPatternIndexer
from src.trellis.utils import resolve_code_graph_db


@pytest.fixture(scope="module")
def project_path():
    path = Path(__file__).resolve().parents[1] / "tui-image-editor-fixture"
    if not path.exists():
        pytest.skip("TUI image-editor fixture not present")
    return path


@pytest.fixture(scope="module")
def bridge(project_path):
    b = CodeGraphBridge(str(project_path))
    result = b.sync_project()
    if not result.get("success"):
        pytest.skip(f"Failed to sync TUI fixture: {result}")
    return b


@pytest.fixture(scope="module")
def db_path(project_path):
    return resolve_code_graph_db(str(project_path))


@pytest.fixture(scope="module")
def js_fixture_path():
    return Path(__file__).resolve().parent / "fixtures" / "js-dynamic"


class TestJSPatternIndexer:
    """Unit-level checks on the JS pattern detector."""

    def test_command_registrations_detected(self, js_fixture_path):
        indexer = JsPatternIndexer(str(js_fixture_path))
        indexer.index()
        assert "addIcon" in indexer.commands
        reg = indexer.commands["addIcon"]
        assert reg.execute_symbol == "execute"

    def test_command_execution_edges(self, js_fixture_path):
        indexer = JsPatternIndexer(str(js_fixture_path))
        indexer.index()
        targets = {e.target_name for e in indexer.edges if e.relation == "executes"}
        assert "addIcon" in targets

    def test_instantiation_edges(self, js_fixture_path):
        indexer = JsPatternIndexer(str(js_fixture_path))
        indexer.index()
        targets = {e.target_name for e in indexer.edges if e.relation == "instantiates"}
        assert "Graphics" in targets

    def test_typed_property_mappings(self, js_fixture_path):
        indexer = JsPatternIndexer(str(js_fixture_path))
        indexer.index()
        props = indexer.properties.get("ImageEditor", [])
        names = {p.property_name for p in props}
        assert "_graphics" in names

    def test_property_call_edges(self, js_fixture_path):
        indexer = JsPatternIndexer(str(js_fixture_path))
        indexer.index()
        calls = [e for e in indexer.edges if e.relation == "property_call"]
        targets = {e.target_name for e in calls}
        assert any(t.startswith("Graphics.") for t in targets)

    def test_this_method_call_edges(self, js_fixture_path):
        indexer = JsPatternIndexer(str(js_fixture_path))
        indexer.index()
        calls = [e for e in indexer.edges if e.relation == "this_method_call"]
        targets = {e.target_name for e in calls}
        assert any("_createComponents" in t for t in targets)


class TestJSGraphAugmentor:
    """Checks that synthetic edges are written into the SQLite graph."""

    def test_augmentor_inserts_edges(self, js_fixture_path):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_copy = Path(tmpdir) / "js-dynamic"
            shutil.copytree(
                str(js_fixture_path),
                str(project_copy),
                ignore=shutil.ignore_patterns(".code-graph"),
            )
            b = CodeGraphBridge(str(project_copy))
            b._run_cli("rebuild-index", "--confirm")
            db_path = resolve_code_graph_db(str(project_copy))
            result = JSGraphAugmentor(str(project_copy), str(db_path)).augment()
            total = sum(result.values())
            assert total > 0
            assert result["command_calls"] > 0
            assert result["instantiation_uses"] > 0

    def test_command_call_edges_in_db(self, db_path):
        conn = sqlite3.connect(str(db_path))
        try:
            c = conn.cursor()
            c.execute(
                """
                SELECT COUNT(*) FROM edges e
                JOIN nodes src ON src.id = e.source_id
                JOIN nodes tgt ON tgt.id = e.target_id
                JOIN files sf ON sf.id = src.file_id
                JOIN files tf ON tf.id = tgt.file_id
                WHERE e.relation = 'calls'
                  AND sf.path LIKE '%imageEditor.js'
                  AND tf.path LIKE '%command/addIcon.js'
                  AND tgt.name = 'execute'
                """
            )
            assert c.fetchone()[0] >= 1
        finally:
            conn.close()

    def test_instantiation_edges_in_db(self, db_path):
        conn = sqlite3.connect(str(db_path))
        try:
            c = conn.cursor()
            c.execute(
                """
                SELECT COUNT(*) FROM edges e
                JOIN nodes tgt ON tgt.id = e.target_id
                JOIN files tf ON tf.id = tgt.file_id
                WHERE e.relation = 'calls' AND tgt.name = 'constructor'
                  AND tf.path LIKE '%graphics.js'
                """
            )
            assert c.fetchone()[0] >= 1
        finally:
            conn.close()


class TestTrellisWrappers:
    """End-to-end checks on the Python bridge API."""

    def test_project_map(self, bridge):
        result = bridge.project_map()
        assert result.get("modules")
        assert len(result["modules"]) > 0

    def test_module_overview(self, bridge):
        result = bridge.module_overview("apps/image-editor/src/js")
        assert result.get("files")
        assert len(result["files"]) > 0

    def test_analyze_impact_addIcon(self, bridge):
        result = bridge.analyze_impact("addIcon")
        assert result.get("risk") in ("LOW", "MEDIUM", "HIGH")
        assert result.get("total_callers", 0) >= 1
        caller_files = {
            c["location"]["file_path"]
            for c in result.get("callers", [])
            if c.get("location")
        }
        assert any("action.js" in f for f in caller_files)

    def test_get_call_graph_addIcon(self, bridge):
        result = bridge.get_call_graph("addIcon", direction="callers")
        assert result.get("symbol")
        assert len(result.get("nodes", [])) >= 1

    def test_find_references_graphics(self, bridge):
        result = bridge.find_references("Graphics")
        assert isinstance(result, list)
        assert len(result) >= 1
        refs = {r["location"]["file_path"] for r in result if r.get("location")}
        assert any("imageEditor.js" in f for f in refs)

    def test_find_references_createComponents(self, bridge):
        result = bridge.find_references("_createComponents")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_dead_code_core_classes_not_flagged(self, bridge):
        dead = bridge.find_dead_code()
        assert dead is not None
        flagged = {item.get("name") for item in dead}
        for critical in ("Graphics", "Ui", "Cropper", "ImageEditor"):
            assert critical not in flagged, (
                f"{critical} incorrectly flagged as dead code"
            )

    def test_dependency_graph(self, bridge):
        result = bridge.dependency_graph("apps/image-editor/src/js/action.js")
        assert result.get("file_path")
        assert result.get("depended_by") or result.get("depends_on")

    def test_find_dead_code_returns_list(self, bridge):
        result = bridge.find_dead_code()
        assert isinstance(result, list)
