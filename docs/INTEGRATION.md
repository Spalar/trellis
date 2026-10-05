# Integrating Trellis into Your Application

Trellis runs as a **sidecar service** next to your app. One process exposes three
surfaces on a single port (`17317` by default):

| Surface | Path | For |
|---|---|---|
| REST API | `/projects`, `/graph/...`, `/feature/...`, `/spec/...`, ... | Your application code |
| MCP endpoint | `/mcp` | AI agents (Claude, Cursor, ...) |
| Visualizer UI | `/` | Humans (served by every mode, no extra deployment) |

The project registry is global, so one Trellis service already answers for every
indexed project — `GET /projects` is the "all active projects" view.

## 1. Run the service

**Docker sidecar (recommended for containerized deployments):**

```bash
docker build -t trellis .
docker run -d --name trellis \
  -e TRELLIS_API_KEY=your-secret \
  -p 17317:17317 \
  -v trellis-data:/data \
  trellis
```

Or with the reference setup in `docker-compose.yml` (includes a one-shot
`trellis sync` example for indexing a repo at deploy time).

**Portable binary (Windows, no Python needed):** unzip the release and run
`trellis.exe api` (headless) or `trellis.exe ui` (with browser UI).

**From source:** `python server.py api` (see `python server.py --help`).

## 2. Register a repository

A repo must be indexed and registered before its data is queryable. Mount the
repo into the container **at the same absolute path you register it under**
(indexes live in `<repo>/.code-graph` and persist with the mount):

```bash
docker run --rm -v trellis-data:/data -v /path/to/my-app:/repos/my-app \
  trellis sync /repos/my-app --project-id my-app
```

After the first sync a file watcher keeps the index fresh automatically;
re-run the sync (with `--incremental`) only after large external changes.

## 3. Consume the API

### Python — `trellis-client` SDK

```bash
pip install ./client   # from the Trellis repo; PyPI distribution planned
```

```python
from trellis_client import TrellisClient

with TrellisClient("http://trellis:17317", api_key="your-secret") as t:
    print(t.list_projects())                 # all projects + index/sync status
    graph = t.graph("my-app")                # modules + functions
    impact = t.impact("my-app", "checkout")  # blast radius of a symbol
```

### Any language — raw REST + OpenAPI

Interactive documentation for every endpoint (with try-it-out) is served at
`/docs` (Swagger UI) and `/openapi.json` while the API runs — generate a typed
client for any language from the spec:

```bash
curl -H "Authorization: Bearer your-secret" http://localhost:17317/projects
# {"projects": [{"id": "my-app", "path": "/repos/my-app",
#                "index_exists": true, "nodes": 1146, "files": 109,
#                "sync": {"state": "completed", ...}}, ...]}
```

### Project metrics, removal, and usage stats

```bash
# Full detail for one project: index stats (nodes/files/modules), sync state,
# spec status, notes count + per-tag counts (e.g. "architecture" notes)
curl -H "Authorization: Bearer your-secret" http://localhost:17317/projects/my-app

# Remove a project from the registry (evicts its engine process).
# Add ?delete_data=true to also delete Trellis-side data (notes).
# The repo itself — including its .code-graph index — is never touched.
curl -X DELETE -H "Authorization: Bearer your-secret" \
  http://localhost:17317/projects/my-app

# MCP usage telemetry: total calls, per-tool counts, error counts,
# and the last 50 calls (timestamp, tool, project, duration, ok)
curl -H "Authorization: Bearer your-secret" http://localhost:17317/stats/usage
```

Usage stats persist across restarts in `TRELLIS_DATA_DIR/usage.json` and are
recorded for every MCP tool call (stdio or HTTP transport alike).

### AI agents — MCP over HTTP

Point any MCP client at `http://<host>:17317/mcp` with the bearer key, e.g. for
Claude Desktop / Cursor add to the client config an HTTP transport entry with
URL `http://localhost:17317/mcp` and header `Authorization: Bearer your-secret`.
The same 20 tools available over stdio then work over the network.

## Authentication & network safety

- **Loopback binds** (`127.0.0.1`, the default): strict — Host/Origin are
  checked against a local allowlist (DNS-rebinding/CSRF defense). If
  `TRELLIS_API_KEY` is unset, a random per-launch token is minted and only the
  served UI page receives it.
- **Network binds** (`0.0.0.0` or a non-loopback IP): Host/Origin checks are
  relaxed (the bearer token is the protection) and the token is **always**
  required — the server refuses to start on a network interface without
  `TRELLIS_API_KEY` set. The UI page (`/`) embeds the key, so it too requires
  credentials: open it as `http://<host>:17317/?token=your-secret` — the page
  then uses that token for its API calls. Without credentials `/` returns 401.
- `TRELLIS_ALLOW_NO_AUTH=true` disables the token check for local development
  only; it has no effect on network binds.
- Known peers can be allowlisted extra-hostname style with
  `TRELLIS_TRUSTED_HOSTS=host1,host2` (applies in both modes).
- **UI availability**: every mode serves the visualizer at `/` (the UI is
  static files inside the service — no separate deployment). For genuinely
  headless API processes, set `TRELLIS_UI=off` and `/` returns 404.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TRELLIS_API_KEY` | _(random per launch)_ | Stable bearer token for API/MCP clients |
| `TRELLIS_HOST` | `127.0.0.1` | Bind address; non-loopback requires the key |
| `TRELLIS_PORT` | `17317` | Bind port |
| `TRELLIS_DATA_DIR` | `~/.trellis` | Registry / notes / sync status (use a volume in Docker) |
| `TRELLIS_TRANSPORT` | `stdio` | `http` makes the MCP server speak HTTP (used by the image) |
| `TRELLIS_ALLOW_NO_AUTH` | off | Skip token check, loopback binds only |
| `TRELLIS_TRUSTED_HOSTS` | _(empty)_ | Extra allowed Host/Origin names |
| `TRELLIS_UI` | `on` | `off` makes the API headless (no visualizer at `/`) |
