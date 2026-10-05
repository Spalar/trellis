# Review Brief: 3-Way Split + Integration Readiness (uncommitted changeset)

Purpose: context for reviewing the working-tree changes in this repo. Everything
below is **uncommitted**; the last committed state is the pre-split monolith.

## What was done, in order

### 1. Logo (unrelated, docs only)
- `docs/assets/trellis-logo.svg` — animated minimal vein/trellis SVG (draw-in,
  sap pulses, center heartbeat); `trellis-logo-preview.png` is a static render.

### 2. Monolith split — 3 independently-startable components
`server.py` (~2,490 lines) → ~260-line CLI dispatcher. Shared state extracted to
`src/trellis/core.py` (project resolution, LRU bridge cache, sync jobs, spec
manager, `LocalHttpGuard`). New modules:
- `src/trellis/mcp_server.py` — all 20 FastMCP tools, behavior unchanged.
- `src/trellis/api.py` — full rewrite of the stale FastAPI prototype;
  `create_api_app()`, identical URL paths to the old custom routes.
- `src/trellis/ui.py` — visualizer + mounted API.
- CLI: `python server.py [mcp]` (default, stdio; `TRELLIS_TRANSPORT=http` →
  UI+REST+`/mcp` on one port), `api`, `ui`, `sync <repo> [--project-id]
  [--incremental]`. **No-arg behavior unchanged** — existing MCP client configs
  still work.
- Deleted: `auth.py` (dead, folded into guard), `start_server.py` (stale).
- `visualizer.html` baseHost → same-origin with `?api=` override.
- Packaging: `trellis.spec`, `scripts/build_release.py`, Makefile (`run-api`,
  `run-ui`) updated; pyproject `py-modules` fixed; `fastapi>=0.115` added.

### 3. Trusted-network mode (the Docker enabler)
- `core.LocalHttpGuard`: non-loopback bind (`TRELLIS_HOST=0.0.0.0` etc.) skips
  Host/Origin checks but **always requires the token** (`TRELLIS_ALLOW_NO_AUTH`
  ignored). New `TRELLIS_TRUSTED_HOSTS` extra allowlist; env now read per request.
- `server.py`: refuses non-loopback bind without `TRELLIS_API_KEY` (exit 2).
- Loopback behavior byte-identical to before.

### 4. Cleanup / latent bug fix
- `bridge.py`: duplicate `trace_http_route` — the CLI-only shadow (no `depth`
  param) had silently replaced the MCP version that `trellis_trace_http_route`
  calls with `depth=`; merged into one `_mcp_then_cli` method. **Review this.**
- Removed stale `__version__` in `src/trellis/__init__.py`; synced pyproject to
  0.3.0; unused imports removed. Repo is fully ruff-clean.

### 5. Integration artifacts
- `client/` — `trellis-client` SDK (httpx only; 21 methods, `TrellisError`,
  context manager; publishable pyproject, version 0.1.0).
- `Dockerfile` — multi-stage: `rust:1-bookworm` builds code-graph-mcp from
  `third_party/` (same cargo security patches as `build_bridge.py`,
  `--no-default-features`), runtime `python:3.11-slim`; one process serves
  REST+UI+`/mcp` on 17317; `TRELLIS_DATA_DIR=/data` volume; HEALTHCHECK.
- `docker-compose.yml` — reference sidecar + one-shot sync pattern.
- `.dockerignore` slimmed; `docs/INTEGRATION.md` (new) + GUIDE/ARCHITECTURE
  updated.

### 6. API metrics, detail, removal
- `src/trellis/usage.py` — MCP call telemetry, persisted to
  `TRELLIS_DATA_DIR/usage.json` (thread-locked, atomic writes, ring of last 50).
  Hook: FastMCP middleware (`_UsageRecordingMiddleware` in mcp_server.py) —
  tool bodies untouched; record failures can't break tool calls.
- Endpoints: `GET /projects/{id}` (index nodes/files/modules, sync, spec, notes
  total + per-tag counts), `DELETE /projects/{id}?delete_data=false`
  (unregisters + evicts bridge; `delete_data=true` also removes Trellis-side
  data; **never touches the repo or its `.code-graph`**), `GET /stats/usage`.
- `utils.unregister_project`, `core.evict_bridge` added.
- SDK: `get_project`, `delete_project`, `usage_stats`.

## Verification state (all green at handoff)
- `pytest`: **156 passed, 11 skipped** (pre-existing skips). New: `test_api.py`
  (14), `test_guard_network.py` (12), `test_usage.py`, `test_projects_admin.py`,
  `client/tests/test_client.py` (14).
- `ruff check .`: clean (0 errors).
- Smoked live: 4 start modes; network guard (foreign Host → 401/200; no-key
  network bind → exit 2); Docker image built (~394 MB) and ran healthy with
  REST + MCP `initialize` handshake + SDK over the network.

## Review pointers (highest value first)
1. `src/trellis/bridge.py` `trace_http_route` merge — semantic change to a
   previously-broken path; confirm MCP-first/CLI-fallback matches intent.
2. `src/trellis/core.py` guard — security-sensitive; check trusted-mode logic
   and per-request env reads.
3. `DELETE /projects/{id}` — confirm the never-touch-repo contract and the
   `delete_data` scope.
4. `Dockerfile` — pinned crate list duplicates `build_bridge.py` (must stay in
   sync with `security_scan.py` findings).
5. `src/trellis/usage.py` — concurrency + corrupt-file recovery.

## Known follow-ups (not done)
- PyPI publish of `client/`; K8s manifests; visualizer UI buttons for the new
  delete/stats endpoints; commit this changeset.
