from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from models import FeatureRecord, FunctionRecord, GraphIndex


class SQLiteGraphStore:
    """SQLite-backed graph store with batch writes, file hashing, and parallel-safe connections."""

    def __init__(self, db_path: str = ".trellis/trellis.db") -> None:
        configured = os.getenv("TRELLIS_DB_PATH", "").strip()
        self.db_path = Path(configured or db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection (each thread gets its own)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent reads
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def close(self) -> None:
        """Close all database connections for this thread."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    @contextmanager
    def _transaction(self):
        """Context manager for transactions."""
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._transaction() as conn:
            # Projects table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    total_features INTEGER DEFAULT 0,
                    total_functions INTEGER DEFAULT 0
                )
            """)

            # Features table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    feature_name TEXT NOT NULL,
                    functions TEXT NOT NULL,  -- JSON array
                    dependencies TEXT NOT NULL,  -- JSON array
                    intent TEXT DEFAULT '',
                    files TEXT NOT NULL,  -- JSON array
                    UNIQUE(project_id, feature_name),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                )
            """)

            # Functions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS functions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    function_path TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    feature_name TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    docstring TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    callers TEXT NOT NULL,  -- JSON array
                    callees TEXT NOT NULL,  -- JSON array
                    UNIQUE(project_id, function_path),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                )
            """)

            # File hashes for incremental sync
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_hashes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    last_modified REAL NOT NULL,
                    UNIQUE(project_id, file_path),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                )
            """)

            # Call graph index for fast lookups
            conn.execute("""
                CREATE TABLE IF NOT EXISTS call_graph (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    caller TEXT NOT NULL,
                    callee TEXT NOT NULL,
                    UNIQUE(project_id, caller, callee),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                )
            """)

            # Create indexes for performance
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_features_project ON features(project_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_features_name ON features(project_id, feature_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_functions_project ON functions(project_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_functions_path ON functions(project_id, function_path)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_functions_feature ON functions(project_id, feature_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_call_graph_project ON call_graph(project_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_call_graph_caller ON call_graph(project_id, caller)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_call_graph_callee ON call_graph(project_id, callee)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_file_hashes_project ON file_hashes(project_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_functions_file_path ON functions(project_id, file_path)
            """)

    def clear_project(self, project_id: str) -> None:
        """Remove all data for a project."""
        with self._transaction() as conn:
            conn.execute("DELETE FROM projects WHERE project_id = ? COLLATE NOCASE", (project_id,))
            # Cascading deletes will clean up related tables

    def save_index(self, project_id: str, index: GraphIndex) -> None:
        """Save project index."""
        with self._transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO projects (project_id, updated_at, total_features, total_functions)
                VALUES (?, ?, ?, ?)
            """, (project_id, index.updated_at, index.total_features, index.total_functions))

    def load_index(self, project_id: str) -> Optional[GraphIndex]:
        """Load project index (case-insensitive lookup)."""
        conn = self._get_conn()
        # Try exact match first, then case-insensitive
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ? COLLATE NOCASE", (project_id,)
        ).fetchone()
        if row is None:
            return None
        return GraphIndex(
            project_id=row["project_id"],
            updated_at=row["updated_at"],
            total_features=row["total_features"],
            total_functions=row["total_functions"],
        )

    def save_feature(self, project_id: str, feature: FeatureRecord) -> str:
        """Save a feature record."""
        with self._transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO features 
                (project_id, feature_name, functions, dependencies, intent, files)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                project_id,
                feature.feature_name,
                json.dumps(feature.functions),
                json.dumps(feature.dependencies),
                feature.intent,
                json.dumps(feature.files),
            ))
        return feature.feature_name

    def load_feature(self, project_id: str, feature_name: str) -> Optional[FeatureRecord]:
        """Load a feature record."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM features WHERE project_id = ? COLLATE NOCASE AND feature_name = ?",
            (project_id, feature_name),
        ).fetchone()
        if row is None:
            return None
        return FeatureRecord(
            feature_name=row["feature_name"],
            functions=json.loads(row["functions"]),
            dependencies=json.loads(row["dependencies"]),
            intent=row["intent"],
            files=json.loads(row["files"]),
        )

    def list_features(self, project_id: str) -> List[FeatureRecord]:
        """List all features for a project."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM features WHERE project_id = ? COLLATE NOCASE ORDER BY feature_name",
            (project_id,),
        ).fetchall()
        return [
            FeatureRecord(
                feature_name=row["feature_name"],
                functions=json.loads(row["functions"]),
                dependencies=json.loads(row["dependencies"]),
                intent=row["intent"],
                files=json.loads(row["files"]),
            )
            for row in rows
        ]

    def save_function(self, project_id: str, function: FunctionRecord) -> str:
        """Save a function record."""
        with self._transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO functions 
                (project_id, function_path, file_path, feature_name, start_line, end_line, 
                 docstring, source, callers, callees)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project_id,
                function.function_path,
                function.file_path,
                function.feature_name,
                function.start_line,
                function.end_line,
                function.docstring,
                function.source,
                json.dumps(function.callers),
                json.dumps(function.callees),
            ))
        return function.function_path

    def load_function(self, project_id: str, function_path: str) -> Optional[FunctionRecord]:
        """Load a function record."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM functions WHERE project_id = ? COLLATE NOCASE AND function_path = ?",
            (project_id, function_path),
        ).fetchone()
        if row is None:
            return None
        return FunctionRecord(
            function_path=row["function_path"],
            file_path=row["file_path"],
            feature_name=row["feature_name"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            docstring=row["docstring"],
            source=row["source"],
            callers=json.loads(row["callers"]),
            callees=json.loads(row["callees"]),
        )

    def load_functions_batch(self, project_id: str, function_paths: List[str]) -> List[FunctionRecord]:
        """Load multiple functions efficiently."""
        if not function_paths:
            return []
        conn = self._get_conn()
        placeholders = ",".join("?" * len(function_paths))
        rows = conn.execute(
            f"SELECT * FROM functions WHERE project_id = ? COLLATE NOCASE AND function_path IN ({placeholders})",
            (project_id, *function_paths),
        ).fetchall()
        return [
            FunctionRecord(
                function_path=row["function_path"],
                file_path=row["file_path"],
                feature_name=row["feature_name"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                docstring=row["docstring"],
                source=row["source"],
                callers=json.loads(row["callers"]),
                callees=json.loads(row["callees"]),
            )
            for row in rows
        ]

    def get_functions_by_file_path(self, project_id: str, file_path: str) -> List[FunctionRecord]:
        """Load functions by file path using index.
        
        Supports both exact match and suffix match (for git relative paths).
        """
        conn = self._get_conn()
        # Use LIKE for suffix matching since stored paths may be absolute
        # while git diff returns relative paths
        rows = conn.execute(
            "SELECT * FROM functions WHERE project_id = ? COLLATE NOCASE AND (file_path = ? OR file_path LIKE ?)",
            (project_id, file_path, f"%{file_path}"),
        ).fetchall()
        return [
            FunctionRecord(
                function_path=row["function_path"],
                file_path=row["file_path"],
                feature_name=row["feature_name"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                docstring=row["docstring"],
                source=row["source"],
                callers=json.loads(row["callers"]),
                callees=json.loads(row["callees"]),
            )
            for row in rows
        ]

    def list_functions(self, project_id: str) -> List[FunctionRecord]:
        """List all functions for a project."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM functions WHERE project_id = ? COLLATE NOCASE ORDER BY function_path",
            (project_id,),
        ).fetchall()
        return [
            FunctionRecord(
                function_path=row["function_path"],
                file_path=row["file_path"],
                feature_name=row["feature_name"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                docstring=row["docstring"],
                source=row["source"],
                callers=json.loads(row["callers"]),
                callees=json.loads(row["callees"]),
            )
            for row in rows
        ]

    def delete_function(self, project_id: str, function_path: str) -> None:
        """Delete a function record."""
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM functions WHERE project_id = ? COLLATE NOCASE AND function_path = ?",
                (project_id, function_path),
            )

    def delete_feature(self, project_id: str, feature_name: str) -> None:
        """Delete a feature record."""
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM features WHERE project_id = ? COLLATE NOCASE AND feature_name = ?",
                (project_id, feature_name),
            )

    def save_snapshot(self, project_id: str, payload: Dict) -> str:
        """Save a snapshot (stored as JSON in a separate snapshots directory)."""
        snapshot_dir = self.db_path.parent / "snapshots"
        snapshot_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = snapshot_dir / f"{project_id}_{stamp}.json"
        with open(snapshot_path, "w") as f:
            json.dump(payload, f, indent=2)
        return str(snapshot_path)

    # ------------------------------------------------------------------
    # File hash management for incremental sync
    # ------------------------------------------------------------------
    def get_file_hash(self, project_id: str, file_path: str) -> Optional[str]:
        """Get stored hash for a file."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT file_hash FROM file_hashes WHERE project_id = ? COLLATE NOCASE AND file_path = ?",
            (project_id, file_path),
        ).fetchone()
        return row["file_hash"] if row else None

    def set_file_hash(self, project_id: str, file_path: str, file_hash: str, last_modified: float) -> None:
        """Store hash for a file."""
        with self._transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO file_hashes (project_id, file_path, file_hash, last_modified)
                VALUES (?, ?, ?, ?)
            """, (project_id, file_path, file_hash, last_modified))

    def get_all_file_hashes(self, project_id: str) -> Dict[str, str]:
        """Load all stored file hashes for a project."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT file_path, file_hash FROM file_hashes WHERE project_id = ? COLLATE NOCASE",
            (project_id,),
        ).fetchall()
        return {row["file_path"]: row["file_hash"] for row in rows}

    def get_changed_files(self, project_id: str, file_hashes: Dict[str, tuple]) -> Dict[str, str]:
        """Compare current file hashes with stored hashes. Returns files that changed or are new."""
        changed = {}
        for file_path, (file_hash, last_modified) in file_hashes.items():
            stored_hash = self.get_file_hash(project_id, file_path)
            if stored_hash != file_hash:
                changed[file_path] = file_hash
                self.set_file_hash(project_id, file_path, file_hash, last_modified)
        return changed

    def delete_file_hashes_not_in(self, project_id: str, file_paths: set) -> None:
        """Remove file hashes for files that no longer exist."""
        with self._transaction() as conn:
            # Get all stored file paths for this project
            rows = conn.execute(
                "SELECT file_path FROM file_hashes WHERE project_id = ? COLLATE NOCASE",
                (project_id,),
            ).fetchall()
            to_delete = [row["file_path"] for row in rows if row["file_path"] not in file_paths]
            for file_path in to_delete:
                conn.execute(
                    "DELETE FROM file_hashes WHERE project_id = ? COLLATE NOCASE AND file_path = ?",
                    (project_id, file_path),
                )

    # ------------------------------------------------------------------
    # Call graph helpers
    # ------------------------------------------------------------------
    def get_callers(self, project_id: str, function_path: str) -> List[str]:
        """Get all callers of a function."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT caller FROM call_graph WHERE project_id = ? COLLATE NOCASE AND callee = ?",
            (project_id, function_path),
        ).fetchall()
        return [row["caller"] for row in rows]

    def get_callees(self, project_id: str, function_path: str) -> List[str]:
        """Get all callees of a function."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT callee FROM call_graph WHERE project_id = ? COLLATE NOCASE AND caller = ?",
            (project_id, function_path),
        ).fetchall()
        return [row["callee"] for row in rows]

    def build_call_graph(self, project_id: str, functions: List[FunctionRecord]) -> None:
        """Build call graph index for fast lookups."""
        with self._transaction() as conn:
            # Clear existing call graph for this project
            conn.execute("DELETE FROM call_graph WHERE project_id = ? COLLATE NOCASE", (project_id,))
            # Insert all edges
            for func in functions:
                for callee in func.callees:
                    conn.execute("""
                        INSERT OR IGNORE INTO call_graph (project_id, caller, callee)
                        VALUES (?, ?, ?)
                    """, (project_id, func.function_path, callee))

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------
    def save_functions_batch(self, project_id: str, functions: List[FunctionRecord]) -> None:
        """Save multiple functions in a single transaction."""
        if not functions:
            return
        with self._transaction() as conn:
            for func in functions:
                conn.execute("""
                    INSERT OR REPLACE INTO functions 
                    (project_id, function_path, file_path, feature_name, start_line, end_line,
                     docstring, source, callers, callees)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    project_id,
                    func.function_path,
                    func.file_path,
                    func.feature_name,
                    func.start_line,
                    func.end_line,
                    func.docstring,
                    func.source,
                    json.dumps(func.callers),
                    json.dumps(func.callees),
                ))

    def save_features_batch(self, project_id: str, features: List[FeatureRecord]) -> None:
        """Save multiple features in a single transaction."""
        if not features:
            return
        with self._transaction() as conn:
            for feature in features:
                conn.execute("""
                    INSERT OR REPLACE INTO features 
                    (project_id, feature_name, functions, dependencies, intent, files)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    project_id,
                    feature.feature_name,
                    json.dumps(feature.functions),
                    json.dumps(feature.dependencies),
                    feature.intent,
                    json.dumps(feature.files),
                ))


# Backward compatibility alias
GraphStore = SQLiteGraphStore
