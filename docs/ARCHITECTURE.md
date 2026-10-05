# Trellis Architecture

## Overview

Trellis is a Python-native workflow layer built on **code-graph-mcp** (Rust MCP server). It adds feature-level impact analysis and knowledge graph capabilities on top of technical code graph analysis.

## Architecture Diagram

```
User Code/Project
       |
       v
Trellis Bridge (Python) ---JSON-RPC---> code-graph-mcp (local binary)
       |                                        |
       |                                        |
       v                                        v
Feature Impact    Local SQLite (.code-graph/)   Local AST Parsing
       |                                        |
       v                                        v
Knowledge Graph  Local FTS5 Search            Call Graph
(.trellis/notes/)                             |
       |                                        |
       v                                        v
MCP Client /      Web UI (visualizer.html)
HTTP Client
```

## Components

### 1. code-graph-mcp (Rust Submodule)

**Repository**: `third_party/code-graph-mcp/`

- **AST Parsing**: Tree-sitter for 16 languages
- **Relation Extraction**: Nodes (functions, classes) + Edges (calls, imports)
- **Storage**: SQLite with FTS5 full-text search
- **API**: JSON-RPC over stdio

**Storage Schema**:

```sql
-- Nodes table
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY,
    file_id INTEGER,
    type TEXT,           -- function, method, class, module
    name TEXT,
    qualified_name TEXT,
    start_line INTEGER,
    end_line INTEGER,
    code_content TEXT,
    signature TEXT,
    doc_comment TEXT
);

-- Edges table
CREATE TABLE edges (
    id INTEGER PRIMARY KEY,
    source_id INTEGER,
    target_id INTEGER,
    relation TEXT        -- imports, calls, belongs_to
);
```

### 2. Trellis Bridge (Python)

**File**: `src/trellis/bridge.py`

Python wrapper around code-graph-mcp with:
- JSON-RPC communication
- Response normalization
- SQLite direct queries (bypasses MCP token limits)
- Graph formatters for visualizer

**Key Methods**:
- `sync_project()` - Index codebase
- `analyze_impact(symbol)` - Technical impact analysis
- `search(query)` - Symbol search
- `get_graph_for_visualizer(max_nodes)` - Graph data for UI; returns a simplified
  view (modules + representative functions) when the project exceeds `max_nodes`
  functions, so large graphs are never dumped in full

### 3. Knowledge Graph (Python)

**File**: `src/trellis/knowledge_graph.py`

Linkable docs note system:
- Markdown notes in `.trellis/notes/`
- Wiki links `[[Note]]`
- Code mentions `@Function`
- Bidirectional backlinks
- YAML frontmatter tags

### 4. Impact Analyzer (Python)

**File**: `src/trellis/impact_analyzer.py`

Combines technical + feature impact:
- Reads custom call edges (workaround for Python call extraction bug)
- Maps functions to features via `project.md`
- Calculates risk levels
- Generates development pointers

### 5. Server (Python)

Three independently-startable components sharing one core, dispatched by `server.py`:

```
python server.py            # MCP only, stdio (default; TRELLIS_TRANSPORT=http for HTTP MCP)
python server.py mcp        # same as above, explicit
python server.py api        # REST API only (FastAPI, /docs), port 17317
python server.py ui         # Visualizer + REST API in one process, port 17317
python server.py sync <repo> [--project-id <id>] [--incremental]
```

- **`src/trellis/core.py`** — shared state used by all three: project-path resolution, the LRU `CodeGraphBridge` cache, sync-job tracking, spec manager, and the `LocalHttpGuard` ASGI middleware (local-host check + bearer token, `TRELLIS_API_KEY` / `TRELLIS_ALLOW_NO_AUTH`).
- **`src/trellis/mcp_server.py`** — FastMCP server (`create_mcp_server`) for AI agents (stdio default; `/mcp` endpoint when HTTP transport is enabled).
- **`src/trellis/api.py`** — FastAPI app (`create_api_app`) with auto-generated OpenAPI docs at `/docs`. This is the integration surface for external tools: `GET /projects` lists every registered project with index and sync status.
- **`src/trellis/ui.py`** — visualizer routes (`/`, `/vendor/*`), mounted into the API app by default so **every mode serves the UI** — no separate deployment (`TRELLIS_UI=off` for headless API processes). `trellis ui` additionally auto-opens the browser; `?api=<url>` still points a UI at a remote API.

**MCP Tools**:
- Code graph: sync, search, get_function, analyze_impact, trace_path, detect_hotspots
- Doc graph: create_note, get_note, search_notes, delete_note, knowledge_graph
- Analysis: get_boundary_map; analyze_diff (opt-in via `TRELLIS_ENABLE_DIFF_ANALYSIS=1`)

**Response guards** (agent context protection):
- Compact JSON with a hard size budget (`TRELLIS_MAX_RESPONSE_CHARS`, default 50000)
- `trellis_get_graph` switches to a simplified view above `max_nodes` (default 200)
- `trellis_knowledge_graph` excludes code nodes unless `include_code=True`
- Long fields truncated: `code_content` (4000 chars), note content (2000 chars)
- `trellis_analyze_diff` (when enabled) caps: `TRELLIS_MAX_DIFF_CHARS` (200k),
  `TRELLIS_MAX_DIFF_FILES` (50), `TRELLIS_MAX_DIFF_FUNCTIONS` (25)

**HTTP Endpoints** (served by `trellis api` and `trellis ui`; all paths are prefixed exactly as shown):
- `GET /projects` - All registered projects with index/sync status (integration entry point)
- `GET /projects/{project_id}` - Full detail: index stats (nodes/files/modules), sync, spec, notes count + per-tag counts
- `DELETE /projects/{project_id}?delete_data=false` - Unregister a project (evicts its engine); `delete_data=true` also removes Trellis-side data. The repo itself is never touched
- `GET /stats/usage` - MCP usage telemetry: total/per-tool/error counts + last 50 calls (persisted across restarts)
- `GET /graph/{project_id}` - Graph data; `POST /graph/{project_id}/sync` - (Re)build index
- `GET /graph/{project_id}/status|tour|health` - Sync state, reading tour, architecture health
- `GET /graph/{project_id}/impact/{symbol}` - Technical impact
- `GET /feature/{project_id}/impact|pointers|context|divergence/{symbol}` - Feature analysis
- `GET|POST /spec/{project_id}`, `GET /spec/{project_id}/alignment` - Feature specs
- `GET /knowledge-graph/{project_id}` - Notes + code; `GET|POST|DELETE /note/{project_id}/{note_id}` - Note CRUD
- `GET /health` - Liveness (no token required, along with `/`)

Everything except `/` and `/health` requires the bearer token (`Authorization: Bearer $TRELLIS_API_KEY`) unless `TRELLIS_ALLOW_NO_AUTH=true`. Interactive API docs are at `/docs` when the API is running.

### 6. Visualizer (HTML/JS)

**File**: `visualizer.html`

Clean web UI:
- Split pane: Editor + Graph
- Code Graph / Doc Graph toggle
- Interactive D3.js force-directed graph
- Impact analysis panel
- Project selector dropdown
- Real-time markdown preview

## Data Flow

### Code Graph Generation

```
Source Files
    |
    v
code-graph-mcp parser (Rust)
    |
    v
SQLite (.code-graph/index.db)
    |
    v
Trellis Bridge (Python)
    |
    v
Visualizer / MCP Tools
```

### Doc Graph Generation

```
User writes markdown
    |
    v
.trellis/notes/*.md
    |
    v
NoteGraph parser (Python)
    |
    v
Wiki links + @mentions resolved
    |
    v
Visualizer / MCP Tools
```

### Impact Analysis

```
Function changed
    |
    v
code-graph-mcp call graph
    |
    v
Trellis Impact Analyzer
    |
    v
project.md feature mapping
    |
    v
Risk level + affected features + pointers
```

## Migration from Old Engine

### What Changed

**Old Way** (Deleted):
```python
from engine import TrellisEngine
from store import GraphStore
from extractor import PythonTreeSitterExtractor

store = GraphStore()
engine = TrellisEngine(store=store, extractor=PythonTreeSitterExtractor())
result = engine.sync_project("my_project", "/path/to/repo")
```

**New Way**:
```python
from src.trellis import CodeGraphBridge

bridge = CodeGraphBridge("/path/to/repo")
bridge.sync_project()
```

### File Mapping

| Old File | Status | New Equivalent |
|----------|--------|----------------|
| `extractor.py` | Deleted | `third_party/code-graph-mcp/src/parser/` |
| `engine.py` | Deleted | `src/trellis/bridge.py` |
| `impact_analyzer.py` | Deleted | `bridge.analyze_impact()` |
| `store.py` | Deleted | `third_party/code-graph-mcp/src/storage/` |
| `server.py` | Updated | Uses bridge + knowledge graph |
| `visualizer.html` | Updated | Adapts to bridge data format |

### API Changes

**Impact Analysis**:
```python
# Old
report = engine.analyze_impact("authenticate_user")
for func in report.impacted_functions:
    print(func.function_path)

# New
impact = bridge.analyze_impact("authenticate_user")
for func in impact.get("affected_functions", []):
    print(func["name"])
    print(func["file_path"])
```

**Search**:
```python
# Old
results = engine.search("authenticate", strategy="semantic")

# New
results = bridge.search("authenticate user")
```

## Security

### External Connections

**code-graph-mcp** (controlled):
1. **Model Download** (optional, disabled) - GitHub releases
2. **Update Check** (opt-in) - GitHub API
3. **Test API Calls** (tests only) - Anthropic/OpenRouter

**Trellis** (none):
- No network calls in production code
- All analysis stays local
- JSON-RPC to local subprocess only

### Controls

- **Network Isolation**: code-graph-mcp runs as local subprocess
- **No Auth Required**: Local development only (`TRELLIS_ALLOW_NO_AUTH`)
- **Data Isolation**: Project data in `~/.trellis/projects/{id}/.code-graph/` and `.trellis/`
- **Build from Source**: Auditable, pinned versions
- **Security Scan**: `python scripts/security_scan.py`

### Data Privacy

What stays local:
- Source code parsing
- AST nodes and call graphs
- Feature specifications
- Search queries
- Impact analysis

## Performance

- **Indexing**: 300+ files/sec (code-graph-mcp Rust)
- **Search**: <100ms (FTS5)
- **Impact Analysis**: <200ms
- **Graph Loading**: <1s for 600+ nodes

## Project Structure

```
trellis/
├── bin/                          # Compiled binary (auto-built)
├── docs/                         # Documentation
│   ├── GUIDE.md                 # User guide
│   ├── ARCHITECTURE.md          # This file
│   ├── MCP_ANALYSIS.md          # MCP architecture analysis
│   └── CODE_GRAPH_ARCHITECTURE.md # code-graph-mcp details
├── scripts/
│   ├── build_bridge.py          # Build from source
│   └── security_scan.py         # Audit submodule
├── skills/                       # OpenCode skills
│   ├── clean-code/
│   └── trellis-mcp/
├── src/trellis/
│   ├── __init__.py
│   ├── bridge.py                # code-graph-mcp wrapper
│   ├── knowledge_graph.py       # Note system
│   ├── impact_analyzer.py       # Impact analysis
│   ├── python_call_indexer.py   # Python call extraction
│   └── feature_impact.py        # Feature context
├── tests/
├── .trellis-data/               # Trellis project storage (created at runtime)
│   └── projects/
│       └── {project-id}/
│           ├── .code-graph/     # Code graph databases
│           └── notes/           # Knowledge notes
├── third_party/
│   └── code-graph-mcp/          # Git submodule
├── visualizer.html              # Web UI
├── server.py                    # CLI dispatcher (mcp | api | ui | sync)
├── src/trellis/
│   ├── core.py                  # Shared state: bridges, projects, guard
│   ├── mcp_server.py            # MCP component (FastMCP tools)
│   ├── api.py                   # REST API component (FastAPI)
│   └── ui.py                    # UI component (visualizer + mounted API)
├── project.md                   # Feature specifications
└── README.md                    # Quick start
```

## Development

### Building code-graph-mcp

```bash
python scripts/build_bridge.py
```

### Updating Submodule

```bash
cd third_party/code-graph-mcp
git fetch origin
git checkout v0.17.4
python scripts/security_scan.py
python scripts/build_bridge.py
```

### Running Tests

```bash
python -m pytest                 # full suite (API, MCP bridge, knowledge graph, ...)
```

## License

MIT
