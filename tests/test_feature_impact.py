"""Tests for feature impact analysis."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.trellis.feature_impact import (
    FeatureImpactAnalyzer,
    ProjectContextParser,
)


@pytest.fixture
def spec_project():
    """Create a temporary project with a project.md spec."""
    project_dir = tempfile.mkdtemp(prefix="trellis-spec-")
    project_path = Path(project_dir)
    (project_path / "project.md").write_text(
        """
# Test Project

## Feature: Authentication

Handles user login and sessions.

### Decisions
- AUTH-001: Use JWT tokens (because: stateless)
  - Constraint: Token expiry must be < 24h
  - Constraint: Refresh tokens stored in httpOnly cookies

### Files
- src/auth/**
- src/middleware/auth*

### Dependencies
- Feature: User Management

## Feature: User Management

Manages users.

### Files
- src/users/**
""",
        encoding="utf-8",
    )

    yield project_path

    try:
        import shutil

        shutil.rmtree(project_dir, ignore_errors=True)
    except Exception:
        pass


def test_project_context_parser(spec_project):
    parser = ProjectContextParser(str(spec_project))
    features = parser.get_all_features()

    assert "Authentication" in features
    assert "User Management" in features

    auth = features["Authentication"]
    assert auth.description == "Handles user login and sessions."
    assert len(auth.decisions) == 1
    assert auth.decisions[0].decision_id == "AUTH-001"
    assert len(auth.decisions[0].constraints) == 2
    assert "src/auth/**" in auth.file_patterns
    assert "User Management" in auth.dependencies


def test_get_feature_for_file(spec_project):
    parser = ProjectContextParser(str(spec_project))

    assert (
        parser.get_feature_for_file("src/auth/login.py").feature_name
        == "Authentication"
    )
    assert (
        parser.get_feature_for_file("src/middleware/authz.py").feature_name
        == "Authentication"
    )
    assert (
        parser.get_feature_for_file("src/users/profile.py").feature_name
        == "User Management"
    )
    assert parser.get_feature_for_file("src/util.py") is None


class MockBridge:
    """Minimal bridge stub for FeatureImpactAnalyzer tests."""

    def __init__(self, symbol_file: str) -> None:
        self._symbol_file = symbol_file

    def get_ast_node(self, symbol: str, **kwargs):
        return {
            "name": symbol,
            "file_path": self._symbol_file,
            "source": "def login(): pass",
        }

    def analyze_impact(self, symbol: str, depth: int = 2):
        return {"callers": [], "risk": "LOW"}


def test_feature_impact_analyzer_maps_to_feature(spec_project):
    bridge = MockBridge("src/auth/login.py")
    analyzer = FeatureImpactAnalyzer(bridge, str(spec_project))

    context = analyzer.get_feature_context("login")
    assert context is not None
    assert context["feature_name"] == "Authentication"
    assert context["status"] == "active"
    assert any("JWT" in d["description"] for d in context["decisions"])


def test_feature_impact_analyzer_unmapped_function(spec_project):
    bridge = MockBridge("src/util.py")
    analyzer = FeatureImpactAnalyzer(bridge, str(spec_project))

    context = analyzer.get_feature_context("helper")
    assert context is None
    warnings = analyzer.check_divergence("helper")
    assert any("not mapped" in w for w in warnings)


def test_development_pointers_include_constraints(spec_project):
    bridge = MockBridge("src/auth/login.py")
    analyzer = FeatureImpactAnalyzer(bridge, str(spec_project))

    pointers = analyzer.get_development_pointers("login")
    pointers_text = "\n".join(pointers)

    assert "Feature: Authentication" in pointers_text
    assert "AUTH-001" in pointers_text
    assert "stateless" in pointers_text
    assert "Token expiry" in pointers_text
