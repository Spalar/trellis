# Trellis - Graph-Based Code Analysis & Knowledge Management

Trellis is a dual-knowledge system that combines **code graph analysis** with **linkable documentation** to help you understand, plan, and verify changes to your codebase.

## Quick Start (Download & Run)

No Python, no terminal commands, no setup required.

1. **Download** the latest release for your platform from GitHub Releases:
   - `trellis-v0.2.0-windows-x86_64.zip`
   - `trellis-v0.2.0-macos-arm64.zip`
   - `trellis-v0.2.0-linux-x86_64.zip`

2. **Extract** the archive

3. **Run the launcher:**
   - Windows: double-click `start-trellis.bat`
   - macOS/Linux: double-click or run `./start-trellis.sh`

4. **Done** — your browser opens to `http://localhost:17317`

The server starts on `http://localhost:17317` with:
- `/` — Web visualizer
- `/mcp` — MCP protocol over HTTP (for AI agents)

---

## What It Does

- **Code Graph**: Auto-indexes your codebase (functions, classes, calls, imports)
- **Watch Mode**: After the first sync, a file watcher re-indexes edited files automatically — no manual re-sync day to day
- **Impact Analysis**: "If I change this function, what breaks?"
- **Reading Tour**: Dependency-ordered module list (foundational first) for onboarding to any codebase
- **Project Health**: Call-graph chokepoints, circular imports, and surprising couplings at a glance
- **Linkable Docs**: Write markdown notes with `[[Wiki Links]]` and `@CodeMentions`
- **MCP Integration**: 20 tools for AI coding agents (Claude, Copilot, etc.)
- **Web UI**: Interactive graph visualizer at `http://localhost:17317`

## Building from Source

If you prefer to build from source or contribute:

### Prerequisites

- **Python 3.10+**
- **Git** (for diff analysis)
- **Rust toolchain** (only if building `code-graph-mcp` from source)

### Install

```bash
git clone <repo-url>
cd trellis
make install
```

This creates `.venv/` and installs all Python dependencies including Trellis itself in editable mode.

### Build code-graph-mcp Binary

Trellis requires the `code-graph-mcp` Rust binary for indexing code:

```bash
# Build from source (requires Rust)
python scripts/build_bridge.py

# Or download pre-built binary from GitHub releases
# Place it in: trellis/bin/code-graph-mcp (or code-graph-mcp.exe on Windows)
```

Verify it's found:
```bash
python -c "from src.trellis.bridge import CodeGraphBridge; print('OK')"
```

## Running the Server (Development)

Trellis supports two transports. Both use **JSON-RPC** for MCP protocol messages:

| Transport | Use Case | Command |
|-----------|----------|---------|
| **stdio** | AI agents / MCP clients | `make dev` |
| **HTTP** | Web UI + MCP over HTTP | `make run-http` |

### Mode 1: MCP Stdio (for AI Agents)

```bash
make dev
```

This starts the MCP server over stdio using JSON-RPC messages. To configure your MCP client, use the setup helper — one command detects your OS, asks which client you use and which server type you want (stdio or HTTP), and prints the correct config with OS-correct paths for Windows, macOS, and Linux:

```bash
make mcp-config                # interactive
# or directly:
python scripts/mcp_setup.py --client codex --transport stdio --print
```

By default it **prints** the config and the exact config-file location so you can verify it works with your client. Once verified, re-run with `--write` to merge it into the client's config file (existing entries are kept; the previous file is backed up to `.bak`).

Supported presets (stdio and HTTP): **Claude Code**, **Claude Desktop**, **OpenAI Codex**, **Kimi Code** (CLI, user or project scope), **VS Code / GitHub Copilot agent mode**, **Cursor**. Pick **"Unknown / other platform"** to get a full linking kit — both transports, the generic `mcpServers` snippet, and every known config location — so any other client can be linked by hand.

**IDE extensions:** the VS Code extensions of Claude Code, Kimi Code, and Codex wrap their respective CLI, so generate the CLI's config — the extension launches the same CLI and picks it up. GitHub Copilot agent mode reads `.vscode/mcp.json` directly (the `vscode` preset). Kimi Code in Zed/JetBrains/Paseo runs via `kimi acp`, which reads `~/.kimi-code/mcp.json` or the project's `.kimi-code/mcp.json` (the `kimi-code` preset).

**Claude Desktop example** (what the generator produces, macOS/Linux paths shown):
```json
{
  "mcpServers": {
    "trellis-core": {
      "command": "/path/to/trellis/.venv/bin/python",
      "args": ["/path/to/trellis/server.py"],
      "env": {
        "TRELLIS_ALLOW_NO_AUTH": "true",
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

**Legacy:** `make trellis-dev-vscode` still prints the VS Code stdio snippet only.

### Mode 2: HTTP Server (for Web UI + MCP over HTTP)

```bash
make run-http
```

Server starts on `http://localhost:17317` with:
- `/` — Web visualizer
- `/health` — Health check
- `/graph/{project_id}` — REST API endpoints
- `/mcp` — MCP protocol over HTTP (JSON-RPC via streamable HTTP/SSE)

**Open visualizer:**
```bash
open http://localhost:17317
# or
make check
```

**Connect an MCP client over HTTP:**
Point your MCP client at `http://localhost:17317/mcp` using the streamable HTTP transport.

## Building a Release

To create a downloadable zip for distribution:

```bash
python scripts/build_release.py
```

The build happens entirely in `out/` (gitignored), so it never collides with
running MCP servers that execute `dist/trellis.exe`. Output:

```
out/trellis-v0.2.0-windows-amd64.zip
out/trellis.exe            # standalone executable
```

To also update the `dist/` runtime location (the exe MCP configs launch):

```bash
python scripts/build_release.py --deploy
```

This copies `trellis.exe`, the `trellis/` folder, and the zip from `out/` into
`dist/`. It fails with a clear message if `dist\trellis.exe` is still running —
close your MCP sessions first.

This bundles Python, all dependencies, the `code-graph-mcp` binary, visualizer HTML, docs, and a launcher script. Users just extract and run.

## Quick Start (After Server is Running)

### 1. Sync a Project

```bash
# Via MCP tool (from your AI agent)
trellis_sync(project_id="my-project", repo_path="/path/to/repo")

# Or via HTTP API
curl http://localhost:17317/graph/my-project
```

**Note:** Project directories should be outside the trellis repo. Trellis stores index data in `~/.trellis/projects/{name}/`, not in your project directory.

**Staying fresh:** after the first sync, the server-side file watcher re-indexes edited files automatically. Re-run `trellis_sync` only after large external changes (huge git pulls, branch switches) or when results look stale.

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

Open `http://localhost:17317` for the interactive visualizer:

- **Code Graph** / **Doc Graph** — explore code and knowledge as connected graphs
- **Tour** — dependency-ordered reading list; the fastest way into an unfamiliar codebase
- **Health** — architecture-health snapshot (chokepoints, circular imports, surprising couplings)
- **Spec** — view/edit `project.md` and verify feature-to-code alignment

## Available Make Commands

| Command | Description |
|---------|-------------|
| `make install` | Install Python dependencies and create venv |
| `make dev` | Start MCP stdio server (for AI agents) |
| `make run-http` | Start HTTP server on port 17317 |
| `make mcp-config` | Interactive MCP client config generator (all providers) |
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
│   ├── build_bridge.py      # Build code-graph-mcp binary
│   └── build_release.py     # Build release archive
├── bin/                     # code-graph-mcp binary (created by build)
├── visualizer.html          # Web UI
├── server.py                # MCP + HTTP server
├── trellis.spec             # PyInstaller build spec
└── Makefile                 # Common commands
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TRELLIS_TRANSPORT` | `stdio` | Server mode: `stdio` or `http` |
| `TRELLIS_HOST` | `127.0.0.1` | HTTP server host |
| `TRELLIS_PORT` | `17317` | HTTP server port |
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

## Manual Evaluation

A standalone evaluation utility lives in `eval/` (gitignored, local-only). It runs a proxy + web UI for manually testing token usage, recall speed, and blast radius with the `tui.image-editor` scenarios.

The `tui-image-editor-fixture/` directory inside this repo is a test fixture used only by Trellis tests; it is not the repo the agent should edit during evaluation. The real repo is `K:\repos\tui.image-editor` (or wherever you cloned it).

```bash
cd eval
pip install -r requirements.txt
python run_eval.py
```

Open `http://127.0.0.1:17417`, select a test case, and follow the on-screen setup instructions to point your coding agent through the proxy. See `eval/README.md` for details.

## License

MIT
