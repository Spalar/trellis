"""Sync auto-generated feature notes from project.md.

Agents should maintain ONE file: project.md. This module materializes a
doc-graph note per parsed feature so the knowledge graph stays connected
without hand-duplicated architecture notes.

Rules:
- A note is created for every feature parsed from project.md that has no note.
- A note carrying the AUTO_MARKER is refreshed when the spec changes.
- An agent-authored note (no marker) is never overwritten — the agent's
  extra knowledge wins over the generated baseline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

AUTO_MARKER = "<!-- auto-generated from project.md; edit project.md, not this note -->"


def feature_note_id(feature_name: str) -> str:
    """Canonical note id for a feature: 'Mask Tool' -> 'feature-mask-tool'."""
    slug = re.sub(r"[^a-z0-9]+", "-", feature_name.lower()).strip("-")
    return f"feature-{slug}"


def _render_note(feature: Any) -> str:
    lines = [
        AUTO_MARKER,
        "",
        f"Architecture note for **{feature.feature_name}**, generated from",
        "`project.md`. Update the spec section; this note follows it.",
        "",
    ]
    if feature.description:
        lines += [feature.description, ""]
    if feature.decisions:
        lines += ["## Decisions", ""]
        for d in feature.decisions:
            entry = f"- {d.decision_id}: {d.description}"
            if d.rationale:
                entry += f" (because: {d.rationale})"
            lines.append(entry)
            for c in d.constraints:
                lines.append(f"  - Constraint: {c}")
        lines.append("")
    if feature.file_patterns:
        lines += ["## Files", ""]
        lines += [f"- `{p}`" for p in feature.file_patterns]
        lines.append("")
    if feature.dependencies:
        lines += ["## Dependencies", ""]
        for dep in feature.dependencies:
            lines.append(f"- [[{feature_note_id(dep)}]]")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def sync_feature_notes(project_path: str) -> Dict[str, Any]:
    """Create or refresh feature-<slug> notes from the parsed project.md.

    Returns a summary dict with per-outcome counts.
    """
    from .feature_impact import ProjectContextParser
    from .knowledge_graph import NoteGraph

    parser = ProjectContextParser(str(project_path))
    features = parser.get_all_features()
    if not features:
        return {"features": 0, "created": 0, "updated": 0, "skipped_agent_authored": 0}

    graph = NoteGraph(str(project_path))
    created = updated = skipped = 0

    for name, feature in features.items():
        note_id = feature_note_id(name)
        content = _render_note(feature)
        existing = graph.get_note(note_id)

        if existing is None:
            graph.save_note(
                note_id, content, title=f"Feature: {name}", tags=["feature"]
            )
            created += 1
        elif AUTO_MARKER in (existing.content or ""):
            if (existing.content or "").strip() != content.strip():
                graph.save_note(
                    note_id, content, title=f"Feature: {name}", tags=["feature"]
                )
                updated += 1
        else:
            skipped += 1  # agent-authored note wins over the generated baseline

    return {
        "features": len(features),
        "created": created,
        "updated": updated,
        "skipped_agent_authored": skipped,
    }
