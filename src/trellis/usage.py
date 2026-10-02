"""MCP tool-call telemetry persisted to the trellis data directory.

Records every FastMCP tool invocation (tool name, project id, outcome,
duration) to <TRELLIS_DATA_DIR>/usage.json. The file is the only state —
there is no in-memory cache, so reads always reflect the latest persisted
document and survive process restarts. All mutations are serialized through
a module-level lock and written atomically (tmp file + os.replace), and any
I/O or parse failure degrades to empty defaults rather than raising.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.trellis.utils import get_trellis_data_dir

_MAX_LAST_CALLS = 50

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _usage_path(data_dir=None) -> Path:
    base = Path(data_dir) if data_dir is not None else get_trellis_data_dir()
    return base / "usage.json"


def _defaults() -> Dict[str, Any]:
    return {
        "total_calls": 0,
        "calls_by_tool": {},
        "errors": {},
        "first_call_at": None,
        "last_call_at": None,
        "last_calls": [],
    }


def _load(data_dir=None) -> Dict[str, Any]:
    """Load the usage doc; corrupt or missing file yields fresh defaults."""
    try:
        with open(_usage_path(data_dir), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return _defaults()
    if not isinstance(doc, dict):
        return _defaults()
    doc = {**_defaults(), **doc}
    for key in ("calls_by_tool", "errors"):
        if not isinstance(doc[key], dict):
            doc[key] = {}
    if not isinstance(doc["last_calls"], list):
        doc["last_calls"] = []
    if not isinstance(doc["total_calls"], int):
        doc["total_calls"] = 0
    return doc


def _persist(doc: Dict[str, Any], data_dir=None) -> None:
    """Write the usage doc atomically so readers never see a partial file."""
    path = _usage_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(tmp, path)


def record_call(tool: str, project_id: str, ok: bool, duration_ms: float) -> None:
    """Record one tool call: bump counters, append the ring entry, persist.

    Never raises: telemetry failures must not break the tool call being
    recorded. On a corrupt/missing usage file the document is reset to
    defaults and the call recorded on top of that.
    """
    try:
        with _lock:
            doc = _load()
            entry = {
                "at": _now_iso(),
                "tool": str(tool),
                "project_id": str(project_id or ""),
                "ok": bool(ok),
                "duration_ms": float(duration_ms),
            }
            tool = str(tool)
            doc["total_calls"] += 1
            doc["calls_by_tool"][tool] = doc["calls_by_tool"].get(tool, 0) + 1
            if not ok:
                doc["errors"][tool] = doc["errors"].get(tool, 0) + 1
            if doc["first_call_at"] is None:
                doc["first_call_at"] = entry["at"]
            doc["last_call_at"] = entry["at"]
            last_calls = doc["last_calls"]
            last_calls.append(entry)
            if len(last_calls) > _MAX_LAST_CALLS:
                del last_calls[: len(last_calls) - _MAX_LAST_CALLS]
            _persist(doc)
    except Exception:
        pass


def get_usage(data_dir=None) -> Dict[str, Any]:
    """Return the current usage doc with every key filled in."""
    return _load(data_dir)


def reset_usage(data_dir=None) -> Dict[str, Any]:
    """Reset telemetry to empty defaults and persist that (for tests)."""
    doc = _defaults()
    try:
        with _lock:
            _persist(doc, data_dir)
    except Exception:
        pass
    return doc
