"""Tests for project.md parsing tolerance (free-form content, bullet props)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.trellis.feature_impact import ProjectContextParser


@pytest.fixture
def freeform_project():
    """Project with general project info alongside feature specs."""
    project_dir = tempfile.mkdtemp(prefix="trellis-freeform-")
    project_path = Path(project_dir)
    (project_path / "project.md").write_text(
        """
# My Project

## Overview

This project is a web service that processes orders.
It has nothing to do with the feature below.

- **Bullet**: This bullet in a free-form section must not leak into features.

## How It Works

Requests arrive at the API layer and are validated.

## Feature: Orders

Handles order intake and processing.

### Files
- src/orders/**

## Feature: Payments

Handles billing.

### Files
- src/payments/**

## Roadmap

- Add reporting
- Add exports
""",
        encoding="utf-8",
    )

    yield project_path

    try:
        import shutil

        shutil.rmtree(project_dir, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def bullet_prop_project():
    """Project using the '- **Field**: value' bullet-prop format."""
    project_dir = tempfile.mkdtemp(prefix="trellis-bullet-")
    project_path = Path(project_dir)
    (project_path / "project.md").write_text(
        """
# Project Name

## Feature: Icons

- **Description**: Renders and manages toolbar icons.
- **Decisions**: SVG icons are preferred over font icons (because: accessibility)
- **Files**: `src/js/component/icon.js`, `src/js/component/iconRegistry.js`
- **Dependencies**: Graphics, Toolbar

## Feature: Graphics

- **Description**: Low-level canvas rendering helpers.
- **Files**: `src/js/graphics.js`
- **Dependencies**: None
""",
        encoding="utf-8",
    )

    yield project_path

    try:
        import shutil

        shutil.rmtree(project_dir, ignore_errors=True)
    except Exception:
        pass


def test_freeform_sections_do_not_pollute_features(freeform_project):
    parser = ProjectContextParser(str(freeform_project))
    features = parser.get_all_features()

    assert set(features.keys()) == {"Orders", "Payments"}
    assert features["Orders"].description == "Handles order intake and processing."
    assert features["Payments"].description == "Handles billing."
    assert "web service" not in features["Orders"].description
    assert "Roadmap" not in features["Orders"].description


def test_freeform_sections_preserve_feature_files(freeform_project):
    parser = ProjectContextParser(str(freeform_project))
    features = parser.get_all_features()

    assert features["Orders"].file_patterns == ["src/orders/**"]
    assert features["Payments"].file_patterns == ["src/payments/**"]


def test_bullet_prop_format_parses(bullet_prop_project):
    parser = ProjectContextParser(str(bullet_prop_project))
    features = parser.get_all_features()

    icons = features["Icons"]
    assert icons.description == "Renders and manages toolbar icons."
    assert icons.file_patterns == [
        "src/js/component/icon.js",
        "src/js/component/iconRegistry.js",
    ]
    assert icons.dependencies == ["Graphics", "Toolbar"]
    assert len(icons.decisions) == 1
    # Decision without a DEC-ID gets a synthesized GEN-### id
    assert icons.decisions[0].decision_id.startswith("GEN-")
    assert "SVG icons" in icons.decisions[0].description
    assert icons.decisions[0].rationale == "accessibility"


def test_bullet_prop_none_dependency_ignored(bullet_prop_project):
    parser = ProjectContextParser(str(bullet_prop_project))
    features = parser.get_all_features()

    assert features["Graphics"].dependencies == []


def test_bullet_prop_feature_file_mapping(bullet_prop_project):
    parser = ProjectContextParser(str(bullet_prop_project))

    assert (
        parser.get_feature_for_file("src/js/component/icon.js").feature_name == "Icons"
    )
    assert (
        parser.get_feature_for_file("src/js/graphics.js").feature_name == "Graphics"
    )
