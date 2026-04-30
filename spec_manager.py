"""Project Spec Manager - handles project.md read/write and integration with docs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from models import utc_now_iso


class ProjectSpec:
    """Represents a project specification from project.md."""

    def __init__(self, project_id: str, content: str = "", source_path: str = "") -> None:
        self.project_id = project_id
        self.content = content
        self.source_path = source_path
        self.updated_at = utc_now_iso()

    @property
    def overview(self) -> str:
        """Extract overview section from spec."""
        return self._extract_section("Overview", "Purpose")

    @property
    def purpose(self) -> str:
        """Extract purpose section from spec."""
        return self._extract_section("Purpose", "Target Users")

    @property
    def architecture(self) -> str:
        """Extract architecture section from spec."""
        return self._extract_section("Architecture", "Key Features")

    @property
    def key_features(self) -> str:
        """Extract key features section from spec."""
        return self._extract_section("Key Features", "Design Decisions")

    @property
    def design_decisions(self) -> str:
        """Extract design decisions section from spec."""
        return self._extract_section("Design Decisions", "Future Directions")

    def _extract_section(self, start_heading: str, end_heading: str) -> str:
        """Extract text between two markdown headings."""
        lines = self.content.splitlines()
        start_idx = None
        end_idx = None

        for i, line in enumerate(lines):
            if line.strip().startswith(f"## {start_heading}"):
                start_idx = i + 1
            elif start_idx is not None and line.strip().startswith(f"## {end_heading}"):
                end_idx = i
                break

        if start_idx is None:
            return ""
        if end_idx is None:
            end_idx = len(lines)

        return "\n".join(lines[start_idx:end_idx]).strip()

    def to_context(self) -> str:
        """Convert spec to a structured context string."""
        sections = []
        if self.overview:
            sections.append(f"OVERVIEW:\n{self.overview}")
        if self.purpose:
            sections.append(f"PURPOSE:\n{self.purpose}")
        if self.architecture:
            sections.append(f"ARCHITECTURE:\n{self.architecture}")
        if self.key_features:
            sections.append(f"KEY FEATURES:\n{self.key_features}")
        return "\n\n---\n\n".join(sections)


class SpecManager:
    """Manages project.md specs for all projects."""

    def __init__(self, base_dir: str = None) -> None:
        if base_dir is None:
            base_dir = os.environ.get("TRELLIS_DATA_DIR", ".trellis/data")
        self.base_dir = Path(base_dir)

    def _safe_name(self, name: str) -> str:
        """Sanitize project name for filesystem."""
        return "".join(c if c.isalnum() or c in "._-" else "_" for c in name).strip("_")

    def _project_dir(self, project_id: str) -> Path:
        """Get project data directory."""
        d = self.base_dir / self._safe_name(project_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load_spec(self, project_id: str) -> Optional[ProjectSpec]:
        """Load project.md for a project."""
        # Check multiple locations
        locations = [
            self._project_dir(project_id) / "project.md",
            self._project_dir(project_id) / "docs" / "project.md",
            Path(".trellis") / "project.md",  # Legacy
        ]

        for path in locations:
            if path.exists():
                return ProjectSpec(
                    project_id=project_id,
                    content=path.read_text(encoding="utf-8"),
                    source_path=str(path),
                )

        return None

    def save_spec(self, project_id: str, content: str) -> Path:
        """Save project.md for a project."""
        path = self._project_dir(project_id) / "project.md"
        path.write_text(content, encoding="utf-8")
        return path

    def spec_exists(self, project_id: str) -> bool:
        """Check if project.md exists."""
        return self.load_spec(project_id) is not None

    def create_template(self, project_id: str, project_purpose: str = "") -> str:
        """Create a project.md template."""
        template = f"""# {project_id} Project Specification

## Overview
{project_purpose or f"Project {project_id}"}

## Purpose
<!-- Describe the core problem this project solves -->

## Target Users
<!-- Who uses this system? -->

## Architecture
<!-- High-level architecture and key components -->

### Core Components
| Component | Responsibility |
|-----------|---------------|
| **Feature A** | Description |
| **Feature B** | Description |

## Key Features
<!-- List major features -->

### 1. Feature Name
Description of feature.

## Design Decisions
<!-- Why key architectural decisions were made -->

## Future Directions
- [ ] Roadmap item 1
- [ ] Roadmap item 2

## Related Features
<!-- Links between features -->

## Constraints
<!-- What this system does NOT do -->
"""
        return template

    def enrich_with_code(self, project_id: str, spec: ProjectSpec, code_summary: str) -> str:
        """Suggest updates to project.md based on code analysis."""
        # For now, return the existing spec
        return spec.content
