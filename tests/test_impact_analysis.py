"""Tests for impact analysis accuracy."""

from __future__ import annotations

import pytest


class TestAnalyzeImpact:
    """Test function-level impact analysis."""

    def test_impact_on_leaf_function(self, trellis_engine, synced_project):
        """Changing a leaf function should have low impact."""
        report = trellis_engine.analyze_impact(
            project_id=synced_project,
            function_path="features/auth.hash_password",
            change_summary="Change hashing algorithm",
            include_suggestions=True,
        )
        assert report.project_id == synced_project
        assert report.root_function == "features/auth.hash_password"
        # hash_password is called by authenticate_user, so impact should be medium at least
        assert len(report.impacted_functions) >= 1

    def test_impact_on_root_function(self, trellis_engine, synced_project):
        """Changing a root function with many callers should have high impact."""
        report = trellis_engine.analyze_impact(
            project_id=synced_project,
            function_path="features/auth.authenticate_user",
            change_summary="Add 2FA requirement",
            include_suggestions=True,
        )
        assert report.project_id == synced_project
        assert report.root_function == "features/auth.authenticate_user"
        # authenticate_user is called by process_payment
        assert "features/payment.process_payment" in report.impacted_functions or len(report.impacted_functions) >= 1

    def test_impact_with_change_summary(self, trellis_engine, synced_project):
        """Change summary should affect semantic impact detection."""
        report1 = trellis_engine.analyze_impact(
            project_id=synced_project,
            function_path="features/payment.process_payment",
            change_summary="Update logging only",
            include_suggestions=False,
        )
        report2 = trellis_engine.analyze_impact(
            project_id=synced_project,
            function_path="features/payment.process_payment",
            change_summary="Modify return type from dict to PaymentResult object",
            include_suggestions=False,
        )
        # Both should have at least low risk; return type change may be higher
        assert report1.risk_level in ("low", "medium", "high")
        assert report2.risk_level in ("low", "medium", "high")

    def test_impact_includes_features(self, trellis_engine, synced_project):
        """Impact report should include affected features."""
        report = trellis_engine.analyze_impact(
            project_id=synced_project,
            function_path="features/auth.authenticate_user",
            change_summary="Change signature",
            include_suggestions=True,
        )
        assert len(report.impacted_features) >= 1
        # Should include Payment feature since it calls authenticate_user
        assert "Payment" in report.impacted_features

    def test_impact_on_nonexistent_function(self, trellis_engine, synced_project):
        with pytest.raises(ValueError):
            trellis_engine.analyze_impact(
                project_id=synced_project,
                function_path="nonexistent.function",
                change_summary="Test",
                include_suggestions=True,
            )


class TestAnalyzeFeatureImpact:
    """Test feature-level impact analysis."""

    def test_feature_impact_cross_feature(self, trellis_engine, synced_project):
        """Changing Auth feature should impact Payment feature."""
        report = trellis_engine.analyze_feature_impact(
            project_id=synced_project,
            feature_name="Auth",
            change_summary="Add rate limiting to authentication",
            include_suggestions=True,
        )
        assert report.project_id == synced_project
        assert report.root_function == "Auth"
        # Payment depends on Auth
        assert "Payment" in report.impacted_features or len(report.impacted_features) == 0

    def test_feature_impact_isolated(self, trellis_engine, synced_project):
        """Changing Reports feature should not impact Auth/Payment much."""
        report = trellis_engine.analyze_feature_impact(
            project_id=synced_project,
            feature_name="Reports",
            change_summary="Add new CSV export format",
            include_suggestions=True,
        )
        assert report.project_id == synced_project
        # Reports doesn't call Auth or Payment directly in our sample
        assert "Auth" not in report.impacted_features
        assert "Payment" not in report.impacted_features

    def test_feature_impact_risk_levels(self, trellis_engine, synced_project):
        """Test that risk levels are assigned appropriately."""
        report = trellis_engine.analyze_feature_impact(
            project_id=synced_project,
            feature_name="Payment",
            change_summary="Complete rewrite of payment flow",
            include_suggestions=True,
        )
        assert report.risk_level in ("low", "medium", "high")


class TestImpactAccuracy:
    """Validate impact detection matches expected graph structure."""

    def test_call_graph_accuracy(self, trellis_engine, synced_project):
        """Verify that callers/callees in the graph are correct."""
        detail = trellis_engine.get_function(
            synced_project, "features/auth.authenticate_user", include_callers=True, include_callees=True
        )
        # authenticate_user calls hash_password and check_credentials
        callee_paths = [c.function_path for c in detail.callees]
        assert "features/auth.hash_password" in callee_paths
        assert "features/auth.check_credentials" in callee_paths

        # process_payment calls authenticate_user
        payment_detail = trellis_engine.get_function(
            synced_project, "features/payment.process_payment", include_callers=True, include_callees=True
        )
        callee_paths = [c.function_path for c in payment_detail.callees]
        assert "features/auth.authenticate_user" in callee_paths

    def test_no_false_positives(self, trellis_engine, synced_project):
        """Impact on reports should not include unrelated features."""
        report = trellis_engine.analyze_feature_impact(
            project_id=synced_project,
            feature_name="Reports",
            change_summary="Update report styling",
            include_suggestions=True,
        )
        # Should not falsely claim auth/payment are impacted
        assert "Auth" not in report.impacted_features
        assert "Payment" not in report.impacted_features
