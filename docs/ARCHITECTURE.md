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
- `get_graph_for_visualizer()` - Graph data for UI

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

**File**: `server.py`

Dual-mode server:
- **MCP Mode**: stdio transport for AI agents (16 tools)
- **HTTP Mode**: FastAPI server for UI (REST endpoints)

**MCP Tools**:
- Code graph: sync, search, get_function, analyze_impact, trace_path, detect_hotspots
- Doc graph: create_note, get_note, search_notes, delete_note, knowledge_graph
- Analysis: analyze_diff, get_boundary_map

**HTTP Endpoints**:
- `/graph/{project_id}` - Graph data
- `/knowledge-graph/{project_id}` - Notes + code
- `/note/{project_id}/{note_id}` - CRUD notes
- `/feature/{project_id}/impact/{symbol}` - Feature impact
- `/projects` - List available projects

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
├── server.py                    # MCP + HTTP server
├── start_server.py              # Launch script
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
# Integration tests
python tests/test_integration.py

# HTTP tests
python tests/test_http_integration.py
```

## License

MIT
