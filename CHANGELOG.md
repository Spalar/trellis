# Changelog

## v0.4.0 — 2026-10-06

Service-oriented release: Trellis splits into independently-startable
components, gains a REST integration surface with a Python SDK and Docker
sidecar, ships a hardened network mode, and upgrades the analysis engine
from 0.156.0 to 0.164.0.

### Added
- **3-way component split** — `trellis [mcp|api|ui|sync]` subcommands; each
  component starts alone. No-subcommand still runs the MCP stdio server, so
  existing MCP client configs work unchanged.
- **REST API admin surface** — `GET /projects` (all projects + index/sync
  status), `GET /projects/{id}` (nodes/files/modules, spec status, notes
  total + per-tag counts), `DELETE /projects/{id}?delete_data=` (unregister
  and evict; never touches the repo), `GET /stats/usage` (MCP call telemetry:
  totals, per-tool/error counts, last 50 calls, persisted across restarts).
- **REST search** — `GET /projects/{id}/search?q=&limit=` (the MCP tool
  `trellis_search_code` worked all along; REST-only integrations had no route).
- **`trellis-client` Python SDK** (`client/`) — 22 methods, `TrellisError`,
  context-manager support; installable with `pip install ./client`.
- **Docker sidecar** — multi-stage `Dockerfile` (Rust engine + slim Python
  runtime) serving REST + UI + `/mcp` on one port; reference
  `docker-compose.yml`; `.dockerignore`.
- **UI in every mode** — the API process serves the visualizer at `/` by
  default; `TRELLIS_UI=off` for headless deployments. No separate UI
  deployment needed.
- **Trusted-network mode** — non-loopback binds (`TRELLIS_HOST=0.0.0.0`)
  relax Host/Origin checks but always require the bearer token; server
  refuses to start on a network interface without `TRELLIS_API_KEY`.
  `TRELLIS_TRUSTED_HOSTS` allowlists extra peers. Browser access to the UI
  on a network bind: `http://host:17317/?token=<key>`.
- Engine **0.156.0 → 0.164.0** (fork merge, 153 upstream commits): Python
  import/decorator/`@overload` fixes, JS/TS member-function nodes,
  route→handler cross-file tracing, running-server index integrity, CLI
  `impact`/`callgraph --node-id`, `fts_only` reporting.

### Security
- **Closed token leak on network binds** — `/` previously served the
  token-embedded UI page without credentials in every mode; on a network
  bind that exposed the API key to anyone reaching the port. `/` now requires
  credentials in trusted mode.
- **Guard now honors `TRELLIS_ALLOW_NO_AUTH`** (previously parsed but
  ignored — loopback binds only, no effect in trusted mode).
- **DELETE endpoint hardening** — URL-supplied project ids validated before
  use as registry keys/paths (path-traversal defense).
- Engine fork: retained both `TRELLIS FORK` patches (symlinked `index.db`
  refusal, freshness no-follow-symlinks) — still unpatched upstream;
  upstream's own `.git`-symlink fix (`bf1148c8`) included.
- Security scan passes clean; `cargo audit` clean.

### Changed
- `server.py` is now a thin CLI dispatcher; code moved to
  `src/trellis/{core,mcp_server,api,ui}.py`. All 20 MCP tools unchanged.
- **Impact risk for no-caller symbols is now `unknown`** (engine behavior;
  previously `low`). UI/clients parsing `risk_level` see the new value.
- **One-time full re-index** on first query after upgrade (engine index
  schema 72 → 113); slower first call per project, then normal.
- `visualizer.html` uses same-origin API by default; `?api=<url>` override.
- Project registry data now also stores usage stats (`usage.json`).

### Removed
- `start_server.py` (stale dev launcher — use `trellis ui`/`trellis api`).
- `auth.py` (dead code; folded into the guard in `core.py`).
- `from src.trellis import app` export (referenced the deleted prototype).

### Breaking changes (read before upgrading)
1. **`start_server.py` is gone.** Deployments using it must switch to
   `trellis ui` (same port, same behavior).
2. **`TRELLIS_ALLOW_NO_AUTH` now actually disables the token check** on
   loopback binds. Dev setups relying on the old (accidental) enforcement
   should set an explicit `TRELLIS_API_KEY`.
3. **UI on a network bind requires credentials** — open it as
   `/?token=<key>`; plain `/` returns 401. Loopback UIs are unchanged.
4. **`risk_level` may be `"unknown"`** where it was `"low"` (engine).
5. **First query per indexed project triggers a full rebuild** after the
   engine upgrade; expect a slow first call (minutes for large repos).
6. `GET /projects` response shape enriched (added `index_exists`, `nodes`,
   `files`, `sync` fields); clients should tolerate new fields.

### Artifacts
- `out/trellis-v0.4.0-windows-amd64.zip` (109.5 MB, includes the REST search
  endpoint from commit `2c0a4ef`).

---

## v0.3.0 and earlier

See `git log` (no changelog was maintained before v0.4.0).
