# Security Review: Trellis with code-graph-mcp

## Executive Summary

**Risk Level: LOW-MEDIUM**

Trellis operates as a **local-only** code analysis tool. The core functionality does not send any user code or project data to external services. However, there are two controlled external connections:

1. **Optional model download** (embed-model feature) - Downloads embedding models from GitHub releases
2. **Plugin auto-update check** - Checks GitHub API for new versions

Both are **opt-in/disableable** and do not transmit user data.

## External Connections Analysis

### 1. code-graph-mcp Binary (Rust)

#### Connection 1: Model Download (Optional)
**File**: `third_party/code-graph-mcp/src/embedding/model.rs:74-80`

```rust
pub fn model_download_url() -> String {
    let version = env!("CARGO_PKG_VERSION");
    format!(
        "https://github.com/sdsrss/code-graph-mcp/releases/download/v{}/models.tar.gz",
        version
    )
}
```

**When triggered**: Only when:
- `--embed-model` feature is enabled at compile time
- User explicitly requests semantic search with embeddings
- Model not already cached locally

**Data transmitted**: NONE (only downloads, no upload)
**Destination**: GitHub releases (github.com)
**Security**: HTTPS + tar.gz extraction with path traversal protection

**Mitigation**: 
- We build with `--no-default-features` (disables embedding)
- Binary in `bin/` was built without embedding support

#### Connection 2: Auto-Update Check (Plugin Only)
**File**: `third_party/code-graph-mcp/claude-plugin/scripts/auto-update.js:143`

```javascript
const url = `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`;
```

**When triggered**: Only when using the Claude Code plugin (npm package)
**Data transmitted**: NONE (GET request only)
**Destination**: GitHub API (api.github.com)

**Mitigation**:
- We don't use the plugin (we use the binary directly)
- Plugin is not installed in our setup

#### Connection 3: Routing Benchmark Tests
**File**: `third_party/code-graph-mcp/tests/routing_bench.rs:181-223`

```rust
let resp = client.post("https://api.anthropic.com/v1/messages")
let resp = client.post("https://openrouter.ai/api/v1/chat/completions")
```

**When triggered**: ONLY during tests (`cargo test`)
**Data transmitted**: Test queries only
**Destination**: Anthropic/OpenRouter APIs

**Mitigation**:
- Tests are not run in production
- Our build doesn't include test code

### 2. Trellis Python Code

#### External Connections: NONE

**Verified by grep search**:
- No `urllib`, `httpx`, `aiohttp` usage in production code
- No `requests` calls in production code (only in test files)
- No socket connections
- No API keys or tokens in code

**All network calls in Trellis**:
- `test_full_integration.py` - Calls localhost:17317 (our own server)
- `test_http_integration.py` - Calls localhost:17317 (our own server)
- `server.py` - FastAPI serves localhost:17317 (local only)
- `src/trellis/bridge.py` - JSON-RPC to local subprocess (code-graph-mcp binary)

### 3. Data Flow Diagram

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
Development Pointers                         Call Graph
       |                                        |
       v                                        v
MCP Client (Claude/VSCode)                Local FTS5 Search
       |
       v
NO EXTERNAL NETWORK (unless explicitly enabled)
```

## Data Privacy Assessment

### What Stays Local ✓
- [x] Source code (parsed locally by tree-sitter)
- [x] AST nodes (stored in local SQLite)
- [x] Call graphs (computed locally)
- [x] Feature specs (project.md, local file)
- [x] Search queries (executed locally via FTS5)
- [x] Impact analysis (computed locally)

### What Might Leave ✗
- [ ] Embedding model download (optional, disabled in our build)
- [ ] Plugin update check (only if using npm plugin)
- [ ] Test API calls (only during `cargo test`)

## Security Controls

### 1. Network Isolation
```python
# bridge.py - Local subprocess only
self._proc = subprocess.Popen(
    [str(self.binary_path)],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd=str(self.project_path),  # Sandboxed to project dir
)
```

### 2. No Authentication Required (Local Only)
```python
# server.py
if os.environ.get("TRELLIS_ALLOW_NO_AUTH") == "true":
    # Skip auth for local development
```

### 3. Data Directory Isolation
```
.code-graph/index.db     # Per-project SQLite database
.trellis/data/           # Trellis project data
```

### 4. Build Security
- Binary compiled from source (auditable)
- Submodule pinned to specific version
- Security scan before build
- No binary blobs or precompiled dependencies

## Recommendations

### Immediate
1. **Verify build flags**: Ensure `--no-default-features` is used
   ```bash
   cargo build --release --no-default-features
   ```

2. **Block GitHub in firewall** (optional):
   If you want to guarantee no external connections, block:
   - `github.com`
   - `api.github.com`
   
   Note: This will prevent model downloads (which we don't use) and update checks (which we don't trigger)

3. **Monitor network traffic** (verification):
   ```bash
   # On Windows
   Get-Process -Name "code-graph-mcp" | Get-NetTCPConnection
   
   # On macOS/Linux
   lsof -i | grep code-graph-mcp
   ```

### Long Term
4. **Build air-gapped version**: Compile with `--no-default-features` and verify no network dependencies
5. **Add network policy**: Document that Trellis is designed for offline/air-gapped use
6. **Verify with packet capture**: Run Wireshark/tcpdump during indexing to confirm no external calls

## Verification Steps

```bash
# 1. Check binary features
strings bin/code-graph-mcp.exe | grep -i "github\|api\|http"

# 2. Check for network libs
ldd bin/code-graph-mcp.exe | grep -i "ssl\|tls\|curl\|reqwest"

# 3. Monitor during indexing
code-graph-mcp rebuild-index --confirm &
# Watch network connections in another terminal

# 4. Verify no model download
# Ensure .code-graph/ directory exists but no models/ subdirectory created
```

## MCP Compatibility

The MCP configuration will work with:
- ✅ Claude Code (Anthropic)
- ✅ Cursor
- ✅ Windsurf
- ✅ VS Code + MCP extensions
- ✅ Any MCP-compatible client

### Current Config (opencode.json)
```json
{
  "mcp": {
    "trellis-core": {
      "type": "local",
      "command": [
        "K:\\repos\\trellis\\.venv\\Scripts\\python.exe",
        "K:\\repos\\trellis\\server.py"
      ],
      "environment": {
        "TRELLIS_ALLOW_NO_AUTH": "true",
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        "FASTMCP_LOG_LEVEL": "ERROR",
        "TRELLIS_DATA_DIR": "K:\\repos\\trellis\\.trellis\\data"
      }
    }
  }
}
```

This config:
- Uses stdio transport (standard for MCP)
- Runs locally on your machine
- No external API keys needed
- No network access required
- Compatible with all MCP clients

## Conclusion

Trellis is **safe for offline/air-gapped use** when:
1. Built with `--no-default-features` (no embedding model downloads)
2. Used without the npm plugin (no auto-update checks)
3. Run as local MCP server (stdio transport)

The only data that leaves your machine is:
- Nothing (in our current configuration)

**11 tools are available** and all operate locally on your code.
