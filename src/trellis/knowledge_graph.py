"""Trellis Knowledge Graph - Linkable docs note system.

Core concepts:
- Notes are markdown files in .trellis/notes/
- [[links]] create edges between notes
- @mentions link code symbols to notes
- Bidirectional: edit note → update graph → render
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from .utils import get_notes_path, resolve_code_graph_db


@dataclass
class Note:
    """A knowledge note with linkable references."""

    id: str
    title: str
    content: str
    path: str
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    is_code_note: bool = False  # Auto-generated from code

    _MENTION_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
    _FILE_MENTION_RE = re.compile(r"@([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)")
    _LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

    @property
    def links(self) -> List[str]:
        """Extract [[links]] from content."""
        return self._LINK_RE.findall(self.content)

    @property
    def mentions(self) -> List[str]:
        """Extract @mentions from content.

        Filters out prose placeholders by requiring at least one code-like
        signal: dotted qualifier, underscore, file extension, CamelCase, or
        all-caps acronym.
        """
        raw = set(self._MENTION_RE.findall(self.content))
        raw.update(self._FILE_MENTION_RE.findall(self.content))
        return [m for m in raw if self._is_code_like(m)]

    @staticmethod
    def _is_code_like(text: str) -> bool:
        """Return True if text looks like a code reference."""
        if "." in text or "_" in text:
            return True
        if text.isupper() and len(text) > 1:
            return True
        upper_count = sum(1 for c in text if c.isupper())
        return upper_count >= 2

    @property
    def backlinks(self) -> List[str]:
        """Notes that link to this note."""
        # Computed by NoteGraph
        return []


@dataclass
class GraphEdge:
    """Edge in knowledge graph."""

    source: str
    target: str
    relation: str  # "links_to", "mentions", "implements", "depends_on"
    context: str = ""  # Surrounding text


class NoteGraph:
    """Knowledge graph manager for linked notes."""

    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        self.notes_dir = get_notes_path(self.project_path)
        self._maybe_migrate_legacy_notes()
        self.notes_dir.mkdir(parents=True, exist_ok=True)

        self.notes: Dict[str, Note] = {}
        self.edges: List[GraphEdge] = []
        self._load_all_notes()

    def _normalize_link(self, text: str) -> str:
        """Normalize a link target to a slug comparable with note IDs."""
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

    def _build_alias_index(self) -> Dict[str, str]:
        """Map common aliases (titles, slugs) to note IDs."""
        aliases: Dict[str, str] = {}

        for note_id, note in self.notes.items():
            aliases[note_id] = note_id
            aliases[note_id.lower()] = note_id

            title = note.title.strip()
            if title:
                aliases[self._normalize_link(title)] = note_id
                aliases[title.lower()] = note_id

                # Strip common prefixes
                for prefix in ("feature:", "decision:", "note:", "func:"):
                    if title.lower().startswith(prefix):
                        rest = title[len(prefix) :].strip()
                        aliases[self._normalize_link(rest)] = note_id
                        aliases[rest.lower()] = note_id

        return aliases

    def _resolve_link(self, target: str) -> Optional[str]:
        """Resolve a [[link]] target to an existing note ID."""
        target_stripped = target.strip()
        if target_stripped in self.notes:
            return target_stripped

        aliases = self._build_alias_index()

        # Exact match
        if target_stripped in aliases:
            return aliases[target_stripped]

        # Case-insensitive exact
        lower = target_stripped.lower()
        if lower in aliases:
            return aliases[lower]

        # Slug match
        slug = self._normalize_link(target_stripped)
        if slug in aliases:
            return aliases[slug]

        # Partial slug match (last resort)
        if slug:
            for alias, note_id in aliases.items():
                if alias.endswith(slug) or slug.endswith(alias):
                    return note_id

        return None

    def _maybe_migrate_legacy_notes(self) -> None:
        """Move notes that were previously stored inside the project directory."""
        legacy_dir = self.project_path / ".trellis" / "notes"
        if not legacy_dir.exists() or legacy_dir == self.notes_dir:
            return

        self.notes_dir.mkdir(parents=True, exist_ok=True)
        for md_file in legacy_dir.glob("*.md"):
            try:
                target = self.notes_dir / md_file.name
                if target.exists():
                    continue
                shutil.move(str(md_file), str(target))
            except OSError:
                pass

        # Remove empty legacy directory
        try:
            legacy_dir.rmdir()
            (self.project_path / ".trellis").rmdir()
        except OSError:
            pass

    def _load_all_notes(self):
        """Load all markdown notes from .trellis/notes/."""
        if not self.notes_dir.exists():
            return

        for md_file in self.notes_dir.glob("*.md"):
            self._load_note(md_file)

    def _load_note(self, path: Path) -> Note:
        """Load single note from file."""
        content = path.read_text(encoding="utf-8")

        # Parse frontmatter if present
        title = path.stem
        tags = []
        created = ""
        updated = ""
        is_code = False

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                content = parts[2].strip()

                # Simple frontmatter parsing
                for line in frontmatter.strip().split("\n"):
                    if ":" in line:
                        key, val = line.split(":", 1)
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key == "title":
                            title = val
                        elif key == "tags":
                            tags = [t.strip() for t in val.split(",")]
                        elif key == "created":
                            created = val
                        elif key == "updated":
                            updated = val
                        elif key == "is_code_note":
                            is_code = val.lower() == "true"

        note = Note(
            id=path.stem,
            title=title,
            content=content,
            path=str(path.relative_to(self.notes_dir.parent)),
            tags=tags,
            created_at=created or datetime.now().isoformat(),
            updated_at=updated or datetime.now().isoformat(),
            is_code_note=is_code,
        )

        self.notes[note.id] = note
        return note

    def save_note(
        self, note_id: str, content: str, title: str = None, tags: List[str] = None
    ) -> Note:
        """Save or update a note."""
        path = self.notes_dir / f"{note_id}.md"

        now = datetime.now().isoformat()

        # Build frontmatter
        frontmatter = ["---"]
        frontmatter.append(f"title: {title or note_id}")
        if tags:
            frontmatter.append(f"tags: {', '.join(tags)}")
        frontmatter.append(f"updated: {now}")
        frontmatter.append("---")
        frontmatter.append("")

        full_content = "\n".join(frontmatter) + content
        path.write_text(full_content, encoding="utf-8")

        # Reload
        return self._load_note(path)

    def get_note(self, note_id: str) -> Optional[Note]:
        """Get note by ID."""
        if note_id in self.notes:
            return self.notes[note_id]

        # Try loading from disk
        path = self.notes_dir / f"{note_id}.md"
        if path.exists():
            return self._load_note(path)

        return None

    def delete_note(self, note_id: str) -> bool:
        """Delete a note."""
        path = self.notes_dir / f"{note_id}.md"
        if path.exists():
            path.unlink()
            if note_id in self.notes:
                del self.notes[note_id]
            return True
        return False

    def build_graph(self, include_code: bool = True) -> Dict:
        """Build graph data for visualization."""
        nodes = []
        edges = []
        unresolved_links: List[Dict[str, str]] = []
        unresolved_mentions: List[Dict[str, str]] = []

        existing_symbols = self._get_existing_symbols() if include_code else set()

        # Add note nodes
        for note_id, note in self.notes.items():
            node_type = "code_note" if note.is_code_note else "note"
            if note.tags:
                if "feature" in note.tags:
                    node_type = "feature"
                elif "decision" in note.tags:
                    node_type = "decision"

            nodes.append(
                {
                    "id": f"note:{note_id}",
                    "type": node_type,
                    "label": note.title,
                    "name": note.title,
                    "content": note.content,
                    "tags": note.tags,
                    "is_code_note": note.is_code_note,
                }
            )

        # Add code nodes if requested
        if include_code:
            nodes.extend(self._get_code_nodes(existing_symbols))

        # Build edges from links and mentions
        for note_id, note in self.notes.items():
            # [[links]] - resolve aliases and link only to existing notes
            for link in note.links:
                resolved = self._resolve_link(link)
                if resolved:
                    edges.append(
                        {
                            "source": f"note:{note_id}",
                            "target": f"note:{resolved}",
                            "type": "links_to",
                        }
                    )
                else:
                    unresolved_links.append({"source": note_id, "target": link})

            # @mentions - only link to symbols that exist in the code graph
            for mention in note.mentions:
                if mention in existing_symbols:
                    edges.append(
                        {
                            "source": f"note:{note_id}",
                            "target": f"func:{mention}",
                            "type": "mentions",
                        }
                    )
                else:
                    unresolved_mentions.append({"source": note_id, "target": mention})

        # Add code edges (functions belong to features)
        if include_code:
            edges.extend(self._get_code_edges(existing_symbols))

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_notes": len(self.notes),
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "unresolved_links": unresolved_links,
                "unresolved_mentions": unresolved_mentions,
            },
        }

    def _get_existing_symbols(self) -> set[str]:
        """Return set of existing code symbol names and qualified names."""
        symbols: set[str] = set()
        db_path = resolve_code_graph_db(str(self.project_path))
        if not db_path.exists():
            return symbols

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT n.name, n.qualified_name
                FROM nodes n
                WHERE n.type IN ('function', 'method', 'class')
            """
            )
            for name, qname in cursor.fetchall():
                if name:
                    symbols.add(name)
                if qname:
                    symbols.add(qname)
            conn.close()
        except Exception:
            pass

        return symbols

    def _get_code_nodes(self, existing_symbols: set[str]) -> List[Dict]:
        """Get code function nodes from code-graph-mcp."""
        db_path = resolve_code_graph_db(str(self.project_path))
        code_nodes = []

        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT n.name, n.qualified_name, f.path, n.start_line, n.end_line, n.type
                FROM nodes n
                JOIN files f ON n.file_id = f.id
                WHERE n.type IN ('function', 'method')
            """)

            seen = set()
            for row in cursor.fetchall():
                name = row[0]
                qname = row[1] or name
                if qname in seen:
                    continue
                seen.add(qname)
                file_path = row[2]

                code_nodes.append(
                    {
                        "id": f"func:{qname}",
                        "type": "function",
                        "label": name,
                        "name": name,
                        "qualified_name": qname,
                        "file_path": file_path,
                        "line": row[3],
                    }
                )

            conn.close()

        return code_nodes

    def _get_code_edges(self, existing_symbols: set[str]) -> List[Dict]:
        """Get edges between code and notes."""
        edges = []

        # Link code notes to their functions
        for note_id, note in self.notes.items():
            if note.is_code_note:
                # Extract function name from note ID (e.g., "func-CodeGraphBridge")
                func_name = note_id.replace("func-", "").replace("-", ".")
                if func_name in existing_symbols:
                    edges.append(
                        {
                            "source": f"note:{note_id}",
                            "target": f"func:{func_name}",
                            "type": "documents",
                        }
                    )

        return edges

    def search_notes(self, query: str) -> List[Note]:
        """Search notes by content."""
        results = []
        query_lower = query.lower()

        for note in self.notes.values():
            if (
                query_lower in note.title.lower()
                or query_lower in note.content.lower()
                or any(query_lower in tag.lower() for tag in note.tags)
            ):
                results.append(note)

        return results

    def search_features(self, query: str) -> List[Note]:
        """Search feature-tagged notes by title or content."""
        query_lower = query.lower()
        results = []

        for note in self.notes.values():
            if "feature" not in note.tags:
                continue
            if (
                query_lower in note.title.lower()
                or query_lower in note.content.lower()
                or any(query_lower in tag.lower() for tag in note.tags)
            ):
                results.append(note)

        return results

    def get_backlinks(self, note_id: str) -> List[str]:
        """Get notes that link to this note (via ID or title aliases)."""
        backlinks = []

        for other_id, other in self.notes.items():
            if other_id == note_id:
                continue
            for link in other.links:
                resolved = self._resolve_link(link)
                if resolved == note_id:
                    backlinks.append(other_id)
                    break

        return backlinks

    def get_related_notes(self, note_id: str) -> List[Dict]:
        """Get related notes (linked + backlinks + mentions)."""
        note = self.notes.get(note_id)
        if not note:
            return []

        related = []
        seen = set()

        # Forward links
        for link in note.links:
            resolved = self._resolve_link(link)
            if resolved and resolved not in seen:
                seen.add(resolved)
                linked = self.notes.get(resolved)
                if linked:
                    related.append(
                        {
                            "id": resolved,
                            "title": linked.title,
                            "relation": "links_to",
                        }
                    )

        # Backlinks
        for backlink in self.get_backlinks(note_id):
            if backlink not in seen:
                seen.add(backlink)
                linked = self.notes.get(backlink)
                if linked:
                    related.append(
                        {
                            "id": backlink,
                            "title": linked.title,
                            "relation": "linked_from",
                        }
                    )

        return related
