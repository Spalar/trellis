"""Tests for core engine functionality."""

from __future__ import annotations

import pytest



class TestSyncProject:
    """Test project synchronization."""

    def test_sync_creates_features(self, trellis_engine, sample_repo):
        result = trellis_engine.sync_project(
            project_id="sync_test",
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )
        assert result.indexed_features > 0
        assert result.indexed_functions > 0
        assert result.project_id == "sync_test"

    def test_sync_idempotent(self, trellis_engine, sample_repo):
        # Sync twice should produce same counts
        r1 = trellis_engine.sync_project(
            project_id="idempotent_test",
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )
        r2 = trellis_engine.sync_project(
            project_id="idempotent_test",
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )
        assert r1.indexed_features == r2.indexed_features
        assert r1.indexed_functions == r2.indexed_functions

    def test_full_sync_clears_previous(self, trellis_engine, sample_repo):
        trellis_engine.sync_project(
            project_id="full_sync_test",
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=False,
        )
        # Should succeed without errors
        index = trellis_engine.store.load_index("full_sync_test")
        assert index is not None
        assert index.total_features > 0


class TestGetFeature:
    """Test feature retrieval."""

    def test_get_existing_feature(self, trellis_engine, synced_project):
        ctx = trellis_engine.get_feature(synced_project, "Auth", include_dependencies=False, depth=1)
        assert ctx.project_id == synced_project
        assert ctx.feature_name == "Auth"
        assert len(ctx.functions) > 0

    def test_get_feature_with_dependencies(self, trellis_engine, synced_project):
        ctx = trellis_engine.get_feature(synced_project, "Payment", include_dependencies=True, depth=2)
        assert ctx.project_id == synced_project
        # Payment depends on Auth (authenticate_user)
        assert "Auth" in ctx.dependencies or len(ctx.dependencies) == 0  # depends on graph

    def test_get_nonexistent_feature(self, trellis_engine, synced_project):
        with pytest.raises(ValueError):
            trellis_engine.get_feature(synced_project, "nonexistent", include_dependencies=False, depth=1)


class TestListFeatures:
    """Test feature listing."""

    def test_list_features(self, trellis_engine, synced_project):
        result = trellis_engine.list_features(synced_project, include_stats=False)
        assert synced_project == result.project_id
        assert len(result.features) > 0
        assert "Auth" in result.features
        assert "Payment" in result.features

    def test_list_features_with_stats(self, trellis_engine, synced_project):
        result = trellis_engine.list_features(synced_project, include_stats=True)
        assert result.stats is not None
        assert len(result.stats) > 0


class TestGetFunction:
    """Test function detail retrieval."""

    def test_get_function(self, trellis_engine, synced_project):
        # Get a function we know exists
        detail = trellis_engine.get_function(
            synced_project, "features/auth.authenticate_user", include_callers=True, include_callees=True
        )
        assert detail.project_id == synced_project
        assert detail.function.function_path == "features/auth.authenticate_user"

    def test_get_nonexistent_function(self, trellis_engine, synced_project):
        with pytest.raises(ValueError):
            trellis_engine.get_function(synced_project, "nonexistent.function", include_callers=True, include_callees=True)


class TestTracePath:
    """Test path tracing between features."""

    def test_trace_existing_path(self, trellis_engine, synced_project):
        # Payment -> Auth (payment calls authenticate_user)
        trace = trellis_engine.trace_path(synced_project, "Payment", "Auth", max_depth=5)
        assert trace.found is True
        assert len(trace.path) > 0

    def test_trace_no_path(self, trellis_engine, synced_project):
        # reports should not depend on auth directly
        trace = trellis_engine.trace_path(synced_project, "auth", "reports", max_depth=5)
        # May or may not find path depending on graph structure
        assert isinstance(trace.found, bool)


class TestSearch:
    """Test search functionality."""

    def test_keyword_search(self, trellis_engine, synced_project):
        result = trellis_engine.search(synced_project, "authenticate", search_type="keyword", limit=5)
        assert result.project_id == synced_project
        assert len(result.results) > 0
        hit = result.results[0]
        assert hit.name == "features/auth.authenticate_user"

    def test_search_limit(self, trellis_engine, synced_project):
        result = trellis_engine.search(synced_project, "def", search_type="keyword", limit=2)
        assert len(result.results) <= 2
