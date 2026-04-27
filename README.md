# trellis

Trellis Core is a focused MCP graph service.

It knows:
- `project_id` as the graph namespace
- API key access control for this MCP server
- feature/function graphs and impact analysis
- feature intent extraction from docstrings and code structure

It does not know:
- users, organizations, billing, or quotas

## Core Files

- `server.py`: FastMCP server and MCP tools
- `auth.py`: bearer API key validation
- `store.py`: project-scoped JSON storage backend
- `extractor.py`: Python, JavaScript, TypeScript AST extraction using tree-sitter
- `engine.py`: graph sync, feature/function context, impact analysis
- `router.py`: in-memory feature index cache
- `models.py`: response and graph models
- `visualizer.py`: graph export & impact subgraph generator
- `visualizer.html`: standalone interactive 2D force-directed graph UI
- `spec_manager.py`: project.md spec read/write/parse
- `.trellis/config.yaml`: optional project configuration (see Configuration section)
- `.env.example`: sample environment variables file

## Visualizer

Trellis includes an interactive 2D graph visualizer styled like Firefox's tracker blocker.

Open it in your browser when the server is running in HTTP mode:
```bash
open http://localhost:17317/visualizer
```

If you need to pick a different project, pass `?project_id=your-project` in the URL.

### What the visualizer shows
- **Feature nodes** (colored rectangles) — each represents a feature with a count badge
- **Function nodes** (colored dots) — each function; color matches its parent feature
- **Feature dependencies** — gray dashed links between feature rectangles
- **Call graph** — cyan glowing links showing which function calls which (disabled by default)
- **Containment** — faint dashed links connecting functions to their feature
- **Project Info** — sidebar shows feature/function counts and project spec status

### Interactions
- **Pan** — drag on empty canvas
- **Zoom** — scroll wheel
- **Select** — click a node to highlight its neighbors
- **Filters** — top-right pills toggle layers (features, functions, deps, calls)
- **Impact Mode** — click "Impact Mode" to view only the upstream callers for a selected function
- **Fit** — click "Fit" to reset the zoom

## MCP Tools

- `trellis_sync` — sync a codebase into the graph
- `trellis_get_feature` — get feature context and dependencies
- `trellis_analyze_impact` — function-level impact analysis
- `trellis_analyze_feature_impact` — **feature-level impact analysis** (all functions in a feature)
- `trellis_trace_path` — trace dependency path between features
- `trellis_search` — search graph metadata
- `trellis_list_features` — list discovered features
- `trellis_get_function` — get function detail with callers/callees
- `trellis_visualize_graph` — get visualizer URL

## Endpoints

- `GET /` — redirects to `/visualizer`
- `GET /visualizer` — graph visualizer HTML app
- `GET /graph/{project_id}` — full graph as D3 nodes/links JSON
- `GET /graph/{project_id}/impact/{function_path}` — impact subgraph JSON
- `GET /spec/{project_id}` — get project.md spec
- `POST /spec/{project_id}` — save project.md spec
- `GET /health` — health check

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

### Option A — Regular server (no Docker)

```bash
# Development (no auth)
make run-http

# Or manually:
TRELLIS_TRANSPORT=http TRELLIS_ALLOW_NO_AUTH=true python server.py

# Production (API key required)
TRELLIS_TRANSPORT=http TRELLIS_API_KEY=your-secret-key python server.py
```

MCP endpoint (HTTP transport):
- `http://localhost:17317/mcp`

Health endpoint:
- `http://localhost:17317/health`

### Option B — Docker

```bash
make compose-up
make check
make compose-down
```

Docker is **optional** — Trellis works fine as a regular Python process on any server or VM.

## Makefile Workflow

```bash
make install
make dev
```

`make dev` runs local stdio in no-auth mode for development.

Generate ready-to-paste VS Code MCP config:

```bash
make trellis-dev-vscode
```

On Windows, stopping `make dev` with Ctrl+C can return a non-zero make exit code even when startup was successful. Treat the startup log line as the health signal for stdio mode.

## Configuration

Trellis has two levels of configuration:

### 1. Project Config (`.trellis/config.yaml`)

Optional per-project configuration file:

```yaml
# Example .trellis/config.yaml
project_name: my-project

extraction:
  languages:
    - python
    - javascript
    - typescript
  exclude:
    - node_modules
    - .venv
    - __pycache__
  include:
    - "*.py"
    - "*.js"
    - "*.ts"

features:
  grouping_strategy: module  # module, directory, or custom

```

A sample config is provided at `.trellis/config.yaml`. Trellis uses sensible defaults, so this file is optional.

### 2. Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
# Edit .env with your settings
```

See `.env.example` for all available server settings.

## Environment

- `TRELLIS_API_KEY`: API key required for authenticated access via `Authorization: Bearer <key>`
- `TRELLIS_ALLOW_NO_AUTH`: optional local-dev override. Set to `true` only for local testing when `TRELLIS_API_KEY` is not set
- `TRELLIS_DATA_DIR`: optional override for graph storage root (default: `.trellis/data`)
- `TRELLIS_TRANSPORT`: `stdio` (default), `http`, or `sse`
- `TRELLIS_HOST`: bind host for HTTP/SSE transport (default: `0.0.0.0`)
- `TRELLIS_PORT`: bind port for HTTP/SSE transport (default: `17317`)
