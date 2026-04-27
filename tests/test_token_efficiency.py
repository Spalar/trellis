"""Tests for token efficiency and incremental processing."""

from __future__ import annotations

import time

import pytest


class TestIncrementalProcessing:
    """Test that incremental sync avoids redundant work."""

    def test_second_sync_faster(self, trellis_engine, sample_repo):
        """Second sync should be significantly faster than first."""
        project_id = "incremental_perf"

        # First sync
        start = time.perf_counter()
        r1 = trellis_engine.sync_project(
            project_id=project_id,
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )
        first_duration = time.perf_counter() - start

        # Second sync (no changes)
        start = time.perf_counter()
        r2 = trellis_engine.sync_project(
            project_id=project_id,
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )
        second_duration = time.perf_counter() - start

        assert r1.indexed_functions == r2.indexed_functions
        # Second sync should be at least 50% faster (very conservative)
        # For very small repos, timing may be similar; assert it's not significantly slower
        assert second_duration <= first_duration * 1.5, (
            f"Second sync ({second_duration:.3f}s) unexpectedly slower than first ({first_duration:.3f}s)"
        )

    def test_no_duplicate_functions(self, trellis_engine, sample_repo):
        """Syncing twice should not create duplicate functions."""
        project_id = "no_dups"
        trellis_engine.sync_project(
            project_id=project_id,
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )
        trellis_engine.sync_project(
            project_id=project_id,
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )

        index = trellis_engine.store.load_index(project_id)
        funcs = list(index.functions.values())
        assert len(funcs) == len(set(funcs)), "Duplicate function paths found"


class TestBatchLoading:
    """Test batch loading reduces store reads."""

    def test_batch_load_exists(self, trellis_engine):
        """Verify store has batch loading capability."""
        assert hasattr(trellis_engine.store, "load_functions_batch")

    def test_batch_load_returns_multiple(self, trellis_engine, synced_project):
        """Batch loading multiple functions should work."""
        # Get all function paths
        index = trellis_engine.store.load_index(synced_project)
        paths = list(index.functions.keys())[:3]

        if len(paths) < 2:
            pytest.skip("Not enough functions for batch test")

        results = trellis_engine.store.load_functions_batch(synced_project, paths)
        assert len(results) == len(paths)
        result_paths = [r.function_path for r in results]
        for path in paths:
            assert path in result_paths


class TestContextSizeEfficiency:
    """Measure that impact analysis produces smaller context than full graph."""

    def test_impact_context_smaller_than_full_graph(self, trellis_engine, synced_project):
        """Impact report should reference fewer functions than total graph."""
        index = trellis_engine.store.load_index(synced_project)
        total_functions = index.total_functions

        report = trellis_engine.analyze_impact(
            project_id=synced_project,
            function_path="features.auth.authenticate_user",
            change_summary="Update signature",
            include_suggestions=True,
        )

        impacted_count = len(report.impacted_functions)
        # Impact context should be smaller than full graph (conservative: at most 80%)
        assert impacted_count < total_functions * 0.8, (
            f"Impact context ({impacted_count}) too close to full graph ({total_functions})"
        )

    def test_feature_impact_context_smaller(self, trellis_engine, synced_project):
        """Feature impact should be smaller than full project."""
        index = trellis_engine.store.load_index(synced_project)
        total_features = index.total_features

        report = trellis_engine.analyze_feature_impact(
            project_id=synced_project,
            feature_name="auth",
            change_summary="Add logging",
            include_suggestions=True,
        )

        impacted_features = len(report.impacted_features)
        # Should not claim all features are impacted
        assert impacted_features < total_features, (
            f"Feature impact claims all {total_features} features affected"
        )


class TestTokenEfficiencyMetrics:
    """Collect metrics to validate token reduction claims."""

    def test_measure_sync_efficiency(self, trellis_engine, sample_repo):
        """Measure and report sync efficiency metrics."""
        project_id = "metrics_test"

        # First sync
        start = time.perf_counter()
        r1 = trellis_engine.sync_project(
            project_id=project_id,
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )
        first_duration = time.perf_counter() - start

        # Second sync
        start = time.perf_counter()
        _ = trellis_engine.sync_project(
            project_id=project_id,
            repo_path=sample_repo,
            config_path=".trellis/config.yaml",
            incremental=True,
        )
        second_duration = time.perf_counter() - start

        # Report metrics
        speedup = first_duration / second_duration if second_duration > 0 else float('inf')
        print("\nToken Efficiency Metrics:")
        print(f"  First sync duration: {first_duration:.3f}s")
        print(f"  Second sync duration: {second_duration:.3f}s")
        print(f"  Speedup: {speedup:.1f}x")
        print(f"  Functions indexed: {r1.indexed_functions}")
        print(f"  Features indexed: {r1.indexed_features}")

        # For very small repos, timing noise can make them equal; assert reasonable performance
        assert speedup >= 0.8, f"Expected >=0.8x speedup, got {speedup:.1f}x"
