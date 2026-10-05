"""Framework-free shared state and helpers for the Trellis servers.

Holds project-id resolution, the LRU-bounded CodeGraphBridge cache, sync-job
bookkeeping (mirrored to disk), the SpecManager singleton, response-budget
helpers, and the local-HTTP guard (host/origin/token checks) shared by the
MCP, API, and UI entry points. Importing this module must not pull in any
web framework.
"""

from __future__ import annotations

import difflib
import hmac
import ipaddress
import json
import os
import secrets
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict
from urllib.parse import parse_qs, urlsplit

from spec_manager import SpecManager

if TYPE_CHECKING:
    from src.trellis import CodeGraphBridge

# Repository root (this file lives at src/trellis/core.py).
TRELLIS_ROOT = Path(__file__).resolve().parent.parent.parent

# ------------------------------------------------------------------
# Response guards (token/perf protection for coding agents)
# ------------------------------------------------------------------

# Hard budget for a single MCP tool response. Oversized payloads are rejected
# with guidance instead of being dumped into the agent's context window.
MAX_RESPONSE_CHARS = int(os.environ.get("TRELLIS_MAX_RESPONSE_CHARS", "50000"))

# Per-field truncation limits for potentially huge values.
MAX_CODE_CONTENT_CHARS = 4000
MAX_NOTE_CONTENT_CHARS = 2000


def dump_json(payload: Any) -> str:
    """Serialize a tool response as compact JSON with a hard size budget.

    Compact separators avoid the ~30-50% token overhead of pretty-printing.
    Payloads beyond MAX_RESPONSE_CHARS are replaced with an actionable error
    so agents narrow the query instead of receiving a context-filling dump.
    """
    text = json.dumps(payload, separators=(",", ":"), default=str)
    if len(text) > MAX_RESPONSE_CHARS:
        return json.dumps(
            {
                "error": "response_too_large",
                "size_chars": len(text),
                "limit_chars": MAX_RESPONSE_CHARS,
                "hint": (
                    "Narrow the query: use a specific module_path, feature_name, "
                    "or function_path, or lower result limits. Raise "
                    "TRELLIS_MAX_RESPONSE_CHARS only if you really need more."
                ),
            }
        )
    return text


def truncate(text: str, limit: int) -> str:
    """Truncate a string field, appending a marker when truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text)} chars total]"


def project_md_status(project_path) -> Dict[str, str]:
    """Report whether the repo root has a project.md feature spec.

    Feature-level analysis (feature_info, trace_path, feature impacts) depends
    on project.md, so agents must create it when missing.
    """
    path = Path(project_path) / "project.md"
    if path.exists():
        return {"status": "ok", "path": str(path)}
    return {
        "status": "missing",
        "path": str(path),
        "required_action": (
            "project.md is missing. You MUST create it before relying on "
            "feature-level analysis: add a '## Feature: <Name>' section per "
            "feature with Description, Decisions, Files (glob patterns), and "
            "Dependencies. See the trellis-mcp skill project.md template."
        ),
    }


def module_entry(m: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a project_map module entry to {path, files, symbols, language}.

    The Rust MCP `project_map` tool reports per-kind counts (functions,
    classes, interfaces_traits, constants, other) and a `languages` list,
    while the CLI fallback parser reports a flat `symbols` count and a
    `language` string. Accept both.
    """
    symbols = m.get("symbols")
    if symbols is None:
        symbols = sum(
            m.get(k, 0) or 0
            for k in (
                "functions",
                "classes",
                "interfaces_traits",
                "constants",
                "other",
            )
        )
    languages = m.get("languages")
    language = (
        ", ".join(str(lang) for lang in languages)
        if languages
        else m.get("language", "")
    )
    return {
        "path": m.get("path", "unknown"),
        "files": m.get("files", 0),
        "symbols": symbols,
        "language": language,
    }


# ------------------------------------------------------------------
# HTTP hardening (CSRF / DNS-rebinding defense for the local server)
# ------------------------------------------------------------------
# The HTTP transports serve on 127.0.0.1 by default. Browser-based attackers
# (malicious websites) can reach localhost servers via simple requests and DNS
# rebinding, so every HTTP request is checked by LocalHttpGuard:
#   1. Host header must be a local hostname — blocks DNS rebinding, and
#      guarantees the token below is only ever served to a local page.
#   2. Token: everything except "/" and "/health" must carry the launch
#      token. TRELLIS_API_KEY is honored as a stable token for MCP-over-HTTP
#      clients; otherwise a random per-launch token is generated and embedded
#      into the served visualizer page (never readable cross-origin, since
#      no CORS headers are ever emitted). TRELLIS_ALLOW_NO_AUTH=true disables
#      the token check entirely (local development only).
#      On trusted-network binds "/" is NOT public: the served page embeds the
#      token, so it requires credentials like everything else. Browsers cannot
#      set headers on navigation, so there "/" also accepts ?token=<key>.
#   3. If an Origin header is present it must be a local origin — blocks
#      cross-site form/fetch CSRF on mutating requests.
#
# Trusted-network mode: when TRELLIS_HOST binds a non-loopback address
# (0.0.0.0/:: or any non-loopback IP — e.g. a Docker sidecar), checks 1 and 3
# are skipped and the bearer token is ALWAYS required (TRELLIS_ALLOW_NO_AUTH
# is ignored). Rebinding/CSRF checks are localhost concerns; on the network
# the token is the only defense, so it cannot be disabled. server.py refuses
# to start in this mode without TRELLIS_API_KEY.
#
# TRELLIS_TRUSTED_HOSTS (comma-separated hostnames/IPs) adds extra allowed
# Host/Origin hostnames on top of the local set in BOTH modes.

http_token = os.environ.get("TRELLIS_API_KEY", "").strip() or secrets.token_hex(16)


def _http_token() -> str:
    """Token enforced by the guard: TRELLIS_API_KEY if set, else launch token."""
    return os.environ.get("TRELLIS_API_KEY", "").strip() or http_token


def current_token() -> str:
    """Token to embed in the served UI page (TRELLIS_API_KEY or launch token).

    Read dynamically so the page always carries the same token the guard
    enforces, even if TRELLIS_API_KEY appeared after import time.
    """
    return _http_token()


def _local_hostnames() -> set[str]:
    """Hostnames allowed in Host/Origin headers: loopback, bind host, extras."""
    hostnames = {"127.0.0.1", "localhost", "::1"}
    hostnames.add(_bind_host())
    for entry in os.environ.get("TRELLIS_TRUSTED_HOSTS", "").split(","):
        entry = entry.strip().lower()
        if entry:
            hostnames.add(entry)
    return hostnames


def _bind_host() -> str:
    """Configured bind host (TRELLIS_HOST) as a bare lowercase hostname/IP."""
    raw = (os.environ.get("TRELLIS_HOST") or "127.0.0.1").strip()
    if raw in ("::", "*"):
        return raw
    return hostname_of(raw)


def is_non_loopback_host(host: str) -> bool:
    """True for wildcard binds and non-loopback IPs; conservative otherwise."""
    host = (host or "").strip()
    if host in ("0.0.0.0", "::", "*"):
        return True
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Unresolvable hostname (e.g. a container name): treat as local.
        return False


def trusted_network_mode() -> bool:
    """True when TRELLIS_HOST binds a non-loopback address (network sidecar)."""
    return is_non_loopback_host(_bind_host())


def _no_auth_enabled() -> bool:
    """TRELLIS_ALLOW_NO_AUTH=true disables the token check (local dev only)."""
    return os.environ.get("TRELLIS_ALLOW_NO_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def hostname_of(host_or_origin: str) -> str:
    """Extract a lowercase hostname from a Host header value or an Origin URL."""
    value = (host_or_origin or "").strip()
    if "://" in value:  # Origin header is a URL
        value = urlsplit(value).netloc
    if value.startswith("["):
        return value[1 : value.index("]")].lower()
    return value.rsplit(":", 1)[0].lower()


class LocalHttpGuard:
    """Pure-ASGI guard; safe for streaming responses (unlike BaseHTTPMiddleware)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        path = scope.get("path", "")
        trusted = trusted_network_mode()

        # Trusted-network binds skip Host/Origin checks (rebinding/CSRF are
        # localhost threats); the bearer token is their only defense.
        if not trusted and hostname_of(headers.get("host", "")) not in _local_hostnames():
            await self._reject(send, 403, "Forbidden: untrusted Host header")
            return

        # Paths that never require a token: /health (liveness) in both modes,
        # and "/" (the UI entry) only on loopback binds — the served page
        # embeds the token, and the Host/Origin checks keep that local-only.
        # On trusted-network binds the token must be presented like for any
        # other path; browsers can't set headers on navigation, so "/" also
        # accepts ?token=<key> there.
        public_paths = {"/health"} if trusted else {"/", "/health"}

        if (
            (trusted or not _no_auth_enabled())
            and path not in public_paths
            and not path.startswith("/vendor/")
        ):
            auth = headers.get("authorization", "")
            token = _http_token()
            token_ok = (
                auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], token)
            ) or (
                bool(headers.get("x-trellis-token"))
                and hmac.compare_digest(headers["x-trellis-token"], token)
            )
            if not token_ok and trusted and path == "/":
                query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
                presented = query.get("token", [""])[0]
                token_ok = bool(presented) and hmac.compare_digest(presented, token)
            if not token_ok:
                await self._reject(
                    send,
                    401,
                    "Unauthorized: missing or invalid token. Open the Trellis UI "
                    "for an authenticated session, or set TRELLIS_API_KEY.",
                )
                return

        origin = headers.get("origin", "")
        if not trusted and origin and hostname_of(origin) not in _local_hostnames():
            await self._reject(send, 403, "Forbidden: untrusted Origin header")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, message: str):
        body = json.dumps({"error": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


# ------------------------------------------------------------------
# State (initialized once at import time)
# ------------------------------------------------------------------
# SpecManager resolves project IDs to repo paths lazily via the lambda, so
# it can be created before resolve_project_path is defined below.
spec_manager = SpecManager(
    project_resolver=lambda project_id: resolve_project_path(project_id)
)

# Cache bridge instances by project path. Bounded: each bridge owns a
# persistent code-graph-mcp subprocess, so an unbounded cache lets an attacker
# (or careless agent) spawn one subprocess per distinct project id.
_BRIDGE_CACHE_MAX = 8
_bridge_cache: "OrderedDict[str, CodeGraphBridge]" = OrderedDict()

# In-memory sync job state, keyed by sanitized project_id (or the cache_key
# when project_id is empty). Authoritative within this process; mirrored to
# disk (sync_status.json) so headless CLI syncs and server restarts are visible
# too — trellis_sync_status prefers this and falls back to the file.
sync_jobs: Dict[str, dict] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_sync_job(source: str) -> dict:
    """Fresh "running" sync record (see write_sync_status for the shape)."""
    return {
        "state": "running",
        "started_at": now_iso(),
        "finished_at": None,
        "detail": "",
        "nodes": None,
        "files": None,
        "source": source,
    }


def set_sync_job(job_key: str, job: dict) -> None:
    """Record sync state in memory and on disk (best-effort, never raises)."""
    sync_jobs[job_key] = job
    from src.trellis.utils import write_sync_status

    write_sync_status(job_key, job)


def _require_registered(resolved: Path) -> None:
    """Reject absolute paths that are not registered projects.

    Without this, an attacker-controlled project id could point the indexer
    at an arbitrary directory (creating .code-graph inside it and spawning
    the indexer with an attacker-chosen cwd).
    """
    from src.trellis.utils import list_registered_projects

    registered = {Path(p).resolve() for p in list_registered_projects().values()}
    if resolved not in registered:
        raise ValueError(
            f"'{resolved}' is not a registered project. Run trellis_sync on it first."
        )


def resolve_project_path(project_id: str, allow_unregistered: bool = False) -> str:
    """Resolve project ID to actual path.

    Searches multiple locations and prefers paths with a .git directory.
    Never resolves to a directory inside the trellis repo to avoid polluting it.
    Absolute paths are honored only for registered projects unless
    allow_unregistered=True (the trellis_sync path, which registers them).
    """
    if project_id == "trellis":
        return str(TRELLIS_ROOT)

    path = Path(project_id)
    trellis_root = TRELLIS_ROOT

    if path.is_absolute() and path.exists():
        resolved = path.resolve()
        # Don't allow resolving to inside trellis unless it's trellis itself
        if not _is_inside_trellis(resolved, trellis_root):
            if not allow_unregistered:
                _require_registered(resolved)
            return str(resolved)

    # 0. Registry: a project_id previously synced (trellis_sync records
    # ~/.trellis/projects/<id>/project.json). Authoritative when the id is
    # not a repo dir the server happens to be running next to — without
    # this, note tools read ~/.trellis/projects/<raw id>/ (empty) while
    # sync wrote under the real repo's dir-name key. Also checked before
    # the CWD heuristics below so a stray same-named subdir can't hijack
    # a registered project.
    from src.trellis.utils import list_registered_projects

    registered_path = list_registered_projects().get(project_id)
    if registered_path and Path(registered_path).exists():
        return registered_path

    # Collect all candidate paths (excluding trellis internals). In frozen
    # (PyInstaller) mode the cwd/sibling/parent heuristics are meaningless —
    # TRELLIS_ROOT is a temp _MEIxxx dir — so only the registry and
    # absolute-path handling apply.
    candidates = []

    if not getattr(sys, "frozen", False):
        # 1. Relative to current directory
        if path.exists():
            resolved = path.resolve()
            if not _is_inside_trellis(resolved, trellis_root):
                candidates.append(resolved)

        # 2. Sibling of trellis root (common pattern: repos/ProjectName)
        sibling = trellis_root.parent / project_id
        if sibling.exists() and sibling.resolve() not in candidates:
            candidates.append(sibling.resolve())

        # 3. Parent of current directory
        parent_sibling = Path.cwd().parent / project_id
        if parent_sibling.exists() and parent_sibling.resolve() not in candidates:
            candidates.append(parent_sibling.resolve())

    # 4. Check if user provided absolute path that doesn't exist yet
    if path.is_absolute():
        if not allow_unregistered:
            _require_registered(path.resolve())
        return str(path)

    if not candidates:
        # Unknown id: refuse to fall back to the raw id — CodeGraphBridge
        # would resolve it against the server CWD and create a phantom
        # <cwd>/<id>/.code-graph directory plus a garbage registry entry.
        hint = (
            f'Register it first: trellis_sync(project_id="{project_id}", '
            'repo_path="<repo root>")'
        )
        close = difflib.get_close_matches(
            project_id, list_registered_projects().keys(), n=3, cutoff=0.5
        )
        if close:
            raise ValueError(
                f"Unknown project '{project_id}'. Did you mean: "
                f"{', '.join(close)}? {hint}"
            )
        raise ValueError(f"Unknown project '{project_id}'. {hint}")

    # Prefer the candidate with a .git directory
    for candidate in candidates:
        if (candidate / ".git").exists():
            return str(candidate)

    # Otherwise return the first candidate
    return str(candidates[0])


def _is_inside_trellis(path: Path, trellis_root: Path) -> bool:
    """Check if a path is inside the trellis repository directory."""
    try:
        path.relative_to(trellis_root)
        return True
    except ValueError:
        return False


def get_bridge(project_id: str, allow_unregistered: bool = False) -> "CodeGraphBridge":
    """Get or create bridge for project (LRU-bounded; evicted bridges close)."""
    if project_id not in _bridge_cache:
        from src.trellis import CodeGraphBridge

        resolved = resolve_project_path(project_id, allow_unregistered)
        _bridge_cache[project_id] = CodeGraphBridge(resolved)
        while len(_bridge_cache) > _BRIDGE_CACHE_MAX:
            _old_key, _old_bridge = _bridge_cache.popitem(last=False)
            try:
                _old_bridge.close()
            except Exception:
                pass
    _bridge_cache.move_to_end(project_id)
    return _bridge_cache[project_id]


def evict_bridge(project_id: str) -> None:
    """Evict a cached bridge for project_id, closing its subprocess (if any).

    Tolerates a project_id that was never cached. Used when a project is
    removed so the bridge cache does not hold a stale index handle.
    """
    bridge = _bridge_cache.pop(project_id, None)
    if bridge is not None:
        try:
            bridge.close()
        except Exception:
            pass


# ------------------------------------------------------------------
# Sync status
# ------------------------------------------------------------------


def _parse_iso(value) -> "datetime | None":
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def sync_guidance(job: dict, index_exists: bool) -> str:
    """One-sentence next-step hint based on sync state and index presence."""
    if job and job.get("state") == "running":
        started = _parse_iso(job.get("started_at"))
        secs = (
            int((datetime.now(timezone.utc) - started).total_seconds())
            if started
            else 0
        )
        return (
            f"Sync is RUNNING (started {secs}s ago). Keep waiting — check this "
            "tool again; do NOT re-issue trellis_sync."
        )
    if job and job.get("state") == "failed":
        return (
            f"Last sync FAILED: {job.get('detail', '')}. Fix and re-run trellis_sync."
        )
    if job and job.get("state") == "completed":
        return "Index ready — proceed with trellis_list_modules etc."
    if not job and not index_exists:
        return (
            "No index yet — run trellis_sync (background mode) or "
            "trellis.exe sync <repo> --project-id <id>."
        )
    return "Index exists and no sync is running."


def project_sync_summary(project_id: str) -> dict:
    """Per-project sync status summary. Blocking — call via asyncio.to_thread."""
    from src.trellis.utils import list_registered_projects, read_sync_status

    try:
        repo = resolve_project_path(project_id)
    except ValueError as e:
        return {"error": str(e)}

    registered = list_registered_projects().get(project_id)
    index_exists = (Path(repo) / ".code-graph" / "index.db").exists()

    nodes = None
    files = None
    if index_exists:
        try:
            bridge = get_bridge(project_id)
            health = bridge.health_check()
            nodes = health.get("nodes_count")
            files = health.get("files_count")
        except Exception:
            # Index present but unreadable right now (e.g. mid-write) — report
            # counts as null rather than failing the whole status call.
            nodes = None
            files = None

    job = sync_jobs.get(project_id) or read_sync_status(project_id)

    return {
        "project_id": project_id,
        "repo_path": repo,
        "registered": registered is not None,
        "index_exists": index_exists,
        "nodes": nodes,
        "files": files,
        "sync": job,
        "guidance": sync_guidance(job, index_exists),
    }
