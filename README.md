# Trellis - Graph-Based Code Analysis & Knowledge Management

Trellis is a dual-knowledge system that combines **code graph analysis** with **linkable documentation** to help you understand, plan, and verify changes to your codebase.

## What It Does

- **Code Graph**: Auto-indexes your codebase (functions, classes, calls, imports)
- **Impact Analysis**: "If I change this function, what breaks?"
- **Linkable Docs**: Write markdown notes with `[[Wiki Links]]` and `@CodeMentions`
- **MCP Integration**: 16 tools for AI coding agents (Claude, Copilot, etc.)
- **Web UI**: Interactive graph visualizer at `http://localhost:17317`

## Prerequisites

- **Python 3.10+**
- **Git** (for diff analysis)
- **Rust toolchain** (only if building `code-graph-mcp` from source)

## Installation

### 1. Clone & Setup

```bash
git clone <repo-url>
cd trellis

# Create virtual environment and install dependencies
make install
```

This creates `.venv/` and installs all Python dependencies including Trellis itself in editable mode.

### 2. Build code-graph-mcp Binary

Trellis requires the `code-graph-mcp` Rust binary for indexing code:

```bash
# Build from source (requires Rust)
python scripts/build_bridge.py

# Or download pre-built binary from GitHub releases
# Place it in: trellis/bin/code-graph-mcp (or code-graph-mcp.exe on Windows)
```

Verify it's found:
```bash
# Should show the binary path
python -c "from src.trellis.bridge import CodeGraphBridge; print('OK')"
```

## Running the Server

Trellis supports two modes:

### Mode 1: MCP Stdio (for AI Agents) - Recommended

For Claude Desktop, VS Code Copilot, or any MCP client:

```bash
make dev
```

This starts the MCP server over stdio. To configure your MCP client:

**VS Code / Copilot Chat:**
```bash
make trellis-dev-vscode
```
This prints the exact JSON config to add to your VS Code MCP settings.

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "trellis-core": {
      "command": "/path/to/trellis/.venv/bin/python",
      "args": ["/path/to/trellis/server.py"],
      "cwd": "/path/to/trellis",
      "env": {
        "TRELLIS_ALLOW_NO_AUTH": "true",
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

### Mode 2: HTTP Server (for Web UI)

For the visualizer and direct API access:

```bash
make run-http
```

Server starts on `http://localhost:17317`

**Open visualizer:**
```bash
open http://localhost:17317
# or
make check
```

## Quick Start

### 1. Sync a Project

```bash
# Via MCP tool (from your AI agent)
trellis_sync(project_id="my-project", repo_path="/path/to/repo")

# Or via HTTP API
curl http://localhost:17317/graph/my-project
```

**Note:** Project directories should be outside the trellis repo. Trellis stores index data in `~/.trellis/projects/{name}/`, not in your project directory.

### 2. Analyze Impact

```bash
# Before changing code, check what breaks
trellis_analyze_impact(project_id="my-project", function_path="authenticate_user")

# Or review a PR
trellis_analyze_diff(project_id="my-project")
```

### 3. Create Documentation

```bash
# Create a knowledge note
trellis_create_note(
    project_id="my-project",
    note_id="auth-decision",
    title="Authentication Architecture",
    content="# Auth Design\n\nWe use JWT tokens. [[Security-Review]]\n\nKey function: @authenticate_user",
    tags="decision, auth"
)
```

### 4. Explore in Browser

Open `http://localhost:17317` for the interactive visualizer.

## Available Make Commands

| Command | Description |
|---------|-------------|
| `make install` | Install Python dependencies and create venv |
| `make dev` | Start MCP stdio server (for AI agents) |
| `make run-http` | Start HTTP server on port 17317 |
| `make trellis-dev-vscode` | Print VS Code MCP configuration |
| `make test` | Run test suite |
| `make lint` | Run ruff linter |
| `make format` | Format code with ruff |
| `make check` | Health check (HTTP mode) |
| `make clean` | Remove build artifacts |

## Documentation

- **[User Guide](docs/GUIDE.md)** - Detailed setup, API reference, writing notes
- **[Architecture](docs/ARCHITECTURE.md)** - Technical details, data flow, security
- **[MCP Skill](skills/trellis-mcp/SKILL.md)** - Tool reference for AI agents

## Project Structure

```
trellis/
├── docs/                    # Documentation
├── skills/trellis-mcp/      # MCP skill definition
├── src/trellis/             # Core Python code
│   ├── bridge.py            # code-graph-mcp integration
│   ├── knowledge_graph.py   # Notes & links
│   ├── impact_analyzer.py   # Impact analysis
│   └── python_call_indexer.py  # Python call graph
├── scripts/                 # Build & utility scripts
├── bin/                     # code-graph-mcp binary (created by build)
├── visualizer.html          # Web UI
├── server.py                # MCP + HTTP server
└── Makefile                 # Common commands
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TRELLIS_TRANSPORT` | `stdio` | Server mode: `stdio` or `http` |
| `TRELLIS_ALLOW_NO_AUTH` | `false` | Disable auth (local dev only) |
| `TRELLIS_DATA_DIR` | `~/.trellis` | Where project indexes are stored |
| `FASTMCP_LOG_LEVEL` | `ERROR` | MCP server log level |

## Troubleshooting

**"code-graph-mcp binary not found"**
```bash
python scripts/build_bridge.py
```

**"GitPython not installed"**
```bash
pip install GitPython
# Or if using venv:
.venv\Scripts\pip install GitPython  # Windows
.venv/bin/pip install GitPython      # macOS/Linux
```

**"DB is locked / process timeout"**
The code-graph-mcp process may be hung. The server auto-detects and kills zombie processes. If issues persist:
```bash
taskkill /F /IM code-graph-mcp.exe  # Windows
pkill -f code-graph-mcp              # macOS/Linux
```

**"No call edges / 0 callers"**
code-graph-mcp's incremental indexing may wipe custom call edges. The server auto-restores them. Force a rebuild:
```bash
trellis_sync(project_id="my-project")
```

## License

MIT
