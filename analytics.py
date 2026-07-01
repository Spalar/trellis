from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class AnalyticsStore:
    """Separate SQLite database for analytics and performance metrics."""

    def __init__(self, db_path: str = ".trellis/analytics.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def close(self) -> None:
        """Close database connection."""
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
        """Create analytics tables."""
        with self._transaction() as conn:
            # Tool call metrics
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    project_id TEXT,
                    duration_ms REAL NOT NULL,
                    status TEXT NOT NULL,  -- success, error, timeout
                    error_message TEXT,
                    params TEXT  -- JSON
                )
            """)

            # Sync metrics
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    functions_indexed INTEGER,
                    features_indexed INTEGER,
                    incremental BOOLEAN,
                    files_parsed INTEGER,
                    files_skipped INTEGER
                )
            """)

            # Performance snapshots
            conn.execute("""
                CREATE TABLE IF NOT EXISTS performance_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    project_id TEXT,
                    operation TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL
                )
            """)

            # Create indexes
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tool_calls_timestamp ON tool_calls(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tool_calls_project ON tool_calls(project_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_metrics_project ON sync_metrics(project_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_perf_project ON performance_snapshots(project_id)
            """)

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------
    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
        status: str = "success",
        project_id: Optional[str] = None,
        error_message: Optional[str] = None,
        params: Optional[Dict] = None,
    ) -> None:
        """Record a tool call with timing."""
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO tool_calls (timestamp, tool_name, project_id, duration_ms, status, error_message, params)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    datetime.now().isoformat(),
                    tool_name,
                    project_id,
                    duration_ms,
                    status,
                    error_message,
                    json.dumps(params) if params else None,
                ),
            )

    def record_sync(
        self,
        project_id: str,
        duration_ms: float,
        functions_indexed: int,
        features_indexed: int,
        incremental: bool,
        files_parsed: int = 0,
        files_skipped: int = 0,
    ) -> None:
        """Record sync operation metrics."""
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO sync_metrics (timestamp, project_id, duration_ms, functions_indexed, features_indexed, incremental, files_parsed, files_skipped)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    datetime.now().isoformat(),
                    project_id,
                    duration_ms,
                    functions_indexed,
                    features_indexed,
                    incremental,
                    files_parsed,
                    files_skipped,
                ),
            )

    def record_metric(
        self,
        operation: str,
        metric_name: str,
        metric_value: float,
        project_id: Optional[str] = None,
    ) -> None:
        """Record a generic performance metric."""
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO performance_snapshots (timestamp, project_id, operation, metric_name, metric_value)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    datetime.now().isoformat(),
                    project_id,
                    operation,
                    metric_name,
                    metric_value,
                ),
            )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------
    def get_tool_call_stats(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get aggregated tool call statistics."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT 
                tool_name,
                COUNT(*) as call_count,
                AVG(duration_ms) as avg_duration,
                MIN(duration_ms) as min_duration,
                MAX(duration_ms) as max_duration,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count
            FROM tool_calls
            WHERE timestamp > datetime('now', ?)
            GROUP BY tool_name
            ORDER BY call_count DESC
        """,
            (f"-{hours} hours",),
        ).fetchall()

        return [
            {
                "tool_name": row["tool_name"],
                "call_count": row["call_count"],
                "avg_duration_ms": round(row["avg_duration"], 2),
                "min_duration_ms": round(row["min_duration"], 2),
                "max_duration_ms": round(row["max_duration"], 2),
                "success_rate": round(row["success_count"] / row["call_count"] * 100, 1)
                if row["call_count"] > 0
                else 0,
                "error_count": row["error_count"],
            }
            for row in rows
        ]

    def get_recent_calls(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent tool calls."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM tool_calls
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (limit,),
        ).fetchall()

        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "tool_name": row["tool_name"],
                "project_id": row["project_id"],
                "duration_ms": row["duration_ms"],
                "status": row["status"],
                "error_message": row["error_message"],
            }
            for row in rows
        ]

    def get_sync_history(
        self, project_id: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get sync operation history."""
        conn = self._get_conn()
        if project_id:
            rows = conn.execute(
                """
                SELECT * FROM sync_metrics
                WHERE project_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM sync_metrics
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (limit,),
            ).fetchall()

        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "project_id": row["project_id"],
                "duration_ms": row["duration_ms"],
                "functions_indexed": row["functions_indexed"],
                "features_indexed": row["features_indexed"],
                "incremental": bool(row["incremental"]),
                "files_parsed": row["files_parsed"],
                "files_skipped": row["files_skipped"],
            }
            for row in rows
        ]

    def get_performance_trends(
        self, operation: str, hours: int = 24
    ) -> List[Dict[str, Any]]:
        """Get performance trends for an operation."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT 
                strftime('%Y-%m-%d %H:00:00', timestamp) as hour,
                AVG(metric_value) as avg_value,
                COUNT(*) as count
            FROM performance_snapshots
            WHERE operation = ? AND timestamp > datetime('now', ?)
            GROUP BY hour
            ORDER BY hour
        """,
            (operation, f"-{hours} hours"),
        ).fetchall()

        return [
            {
                "hour": row["hour"],
                "avg_value": round(row["avg_value"], 2),
                "count": row["count"],
            }
            for row in rows
        ]

    def get_summary_stats(self) -> Dict[str, Any]:
        """Get overall summary statistics."""
        conn = self._get_conn()

        # Total calls
        total_calls = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]

        # Total syncs
        total_syncs = conn.execute("SELECT COUNT(*) FROM sync_metrics").fetchone()[0]

        # Average sync duration
        avg_sync = (
            conn.execute("SELECT AVG(duration_ms) FROM sync_metrics").fetchone()[0] or 0
        )

        # Total functions indexed
        total_functions = (
            conn.execute("SELECT SUM(functions_indexed) FROM sync_metrics").fetchone()[
                0
            ]
            or 0
        )

        # Error rate
        error_rate = (
            conn.execute("""
            SELECT 
                CASE WHEN COUNT(*) > 0 
                THEN CAST(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS REAL) / COUNT(*) * 100
                ELSE 0 END
            FROM tool_calls
        """).fetchone()[0]
            or 0
        )

        return {
            "total_tool_calls": total_calls,
            "total_syncs": total_syncs,
            "avg_sync_duration_ms": round(avg_sync, 2),
            "total_functions_indexed": int(total_functions),
            "error_rate_percent": round(error_rate, 2),
        }
