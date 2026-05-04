"""Trellis Knowledge Graph - Obsidian-like note system.

Core concepts:
- Notes are markdown files in .trellis/notes/
- [[links]] create edges between notes
- @mentions link code symbols to notes
- Bidirectional: edit note → update graph → render
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime


@dataclass
class Note:
    """A knowledge note (like Obsidian)."""
    id: str
    title: str
    content: str
    path: str
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    is_code_note: bool = False  # Auto-generated from code
    
    @property
    def links(self) -> List[str]:
        """Extract [[links]] from content."""
        pattern = r'\[\[([^\]]+)\]\]'
        return re.findall(pattern, self.content)
    
    @property
    def mentions(self) -> List[str]:
        """Extract @mentions from content."""
        pattern = r'@([a-zA-Z_][a-zA-Z0-9_]*)'
        return re.findall(pattern, self.content)
    
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
    """Knowledge graph manager - like Obsidian's graph view."""
    
    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.notes_dir = self.project_path / ".trellis" / "notes"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        
        self.notes: Dict[str, Note] = {}
        self.edges: List[GraphEdge] = []
        self._load_all_notes()
    
    def _load_all_notes(self):
        """Load all markdown notes from .trellis/notes/."""
        if not self.notes_dir.exists():
            return
        
        for md_file in self.notes_dir.glob("*.md"):
            self._load_note(md_file)
    
    def _load_note(self, path: Path) -> Note:
        """Load single note from file."""
        content = path.read_text(encoding='utf-8')
        
        # Parse frontmatter if present
        title = path.stem
        tags = []
        created = ""
        updated = ""
        is_code = False
        
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                content = parts[2].strip()
                
                # Simple frontmatter parsing
                for line in frontmatter.strip().split('\n'):
                    if ':' in line:
                        key, val = line.split(':', 1)
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key == 'title':
                            title = val
                        elif key == 'tags':
                            tags = [t.strip() for t in val.split(',')]
                        elif key == 'created':
                            created = val
                        elif key == 'updated':
                            updated = val
                        elif key == 'is_code_note':
                            is_code = val.lower() == 'true'
        
        note = Note(
            id=path.stem,
            title=title,
            content=content,
            path=str(path.relative_to(self.project_path)),
            tags=tags,
            created_at=created or datetime.now().isoformat(),
            updated_at=updated or datetime.now().isoformat(),
            is_code_note=is_code,
        )
        
        self.notes[note.id] = note
        return note
    
    def save_note(self, note_id: str, content: str, title: str = None, tags: List[str] = None) -> Note:
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
        
        full_content = '\n'.join(frontmatter) + content
        path.write_text(full_content, encoding='utf-8')
        
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
        
        # Add note nodes
        for note_id, note in self.notes.items():
            node_type = "code_note" if note.is_code_note else "note"
            if note.tags:
                if "feature" in note.tags:
                    node_type = "feature"
                elif "decision" in note.tags:
                    node_type = "decision"
            
            nodes.append({
                "id": f"note:{note_id}",
                "type": node_type,
                "label": note.title,
                "name": note.title,
                "content": note.content,
                "tags": note.tags,
                "is_code_note": note.is_code_note,
            })
        
        # Add code nodes if requested
        if include_code:
            nodes.extend(self._get_code_nodes())
        
        # Build edges from links and mentions
        for note_id, note in self.notes.items():
            # [[links]]
            for link in note.links:
                edges.append({
                    "source": f"note:{note_id}",
                    "target": f"note:{link}",
                    "type": "links_to",
                })
            
            # @mentions (link to code symbols)
            for mention in note.mentions:
                edges.append({
                    "source": f"note:{note_id}",
                    "target": f"func:{mention}",
                    "type": "mentions",
                })
        
        # Add code edges (functions belong to features)
        if include_code:
            edges.extend(self._get_code_edges())
        
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_notes": len(self.notes),
                "total_nodes": len(nodes),
                "total_edges": len(edges),
            },
        }
    
    def _get_code_nodes(self) -> List[Dict]:
        """Get code function nodes from code-graph-mcp."""
        # Import here to avoid circular
        try:
            from src.trellis import CodeGraphBridge
            bridge = CodeGraphBridge(str(self.project_path))
            
            # Query DB directly for functions
            import sqlite3
            db_path = self.project_path / ".code-graph" / "index.db"
            code_nodes = []
            
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT n.name, n.qualified_name, f.path, n.start_line, n.end_line, n.type
                    FROM nodes n
                    JOIN files f ON n.file_id = f.id
                    WHERE n.type IN ('function', 'method')
                    LIMIT 100
                """)
                
                for row in cursor.fetchall():
                    name = row[0]
                    qname = row[1] or name
                    file_path = row[2]
                    
                    code_nodes.append({
                        "id": f"func:{qname}",
                        "type": "function",
                        "label": name,
                        "name": name,
                        "file_path": file_path,
                        "line": row[3],
                    })
                
                conn.close()
            
            return code_nodes
        except Exception:
            return []
    
    def _get_code_edges(self) -> List[Dict]:
        """Get edges between code and notes."""
        edges = []
        
        # Link code notes to their functions
        for note_id, note in self.notes.items():
            if note.is_code_note:
                # Extract function name from note ID (e.g., "func-CodeGraphBridge")
                func_name = note_id.replace("func-", "").replace("-", ".")
                edges.append({
                    "source": f"note:{note_id}",
                    "target": f"func:{func_name}",
                    "type": "documents",
                })
        
        return edges
    
    def search_notes(self, query: str) -> List[Note]:
        """Search notes by content."""
        results = []
        query_lower = query.lower()
        
        for note in self.notes.values():
            if (query_lower in note.title.lower() or 
                query_lower in note.content.lower() or
                any(query_lower in tag.lower() for tag in note.tags)):
                results.append(note)
        
        return results
    
    def get_backlinks(self, note_id: str) -> List[str]:
        """Get notes that link to this note."""
        backlinks = []
        target = f"note:{note_id}"
        
        for other_id, other in self.notes.items():
            if other_id == note_id:
                continue
            for link in other.links:
                if link == note_id:
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
            if link not in seen:
                seen.add(link)
                linked = self.notes.get(link)
                if linked:
                    related.append({
                        "id": link,
                        "title": linked.title,
                        "relation": "links_to",
                    })
        
        # Backlinks
        for backlink in self.get_backlinks(note_id):
            if backlink not in seen:
                seen.add(backlink)
                linked = self.notes.get(backlink)
                if linked:
                    related.append({
                        "id": backlink,
                        "title": linked.title,
                        "relation": "linked_from",
                    })
        
        return related
