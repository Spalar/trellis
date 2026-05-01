# Pivot Progress: code-graph-mcp Integration

## Completed

### Infrastructure
- [x] Fork code-graph-mcp added as git submodule
- [x] Build script (scripts/build_bridge.py)
- [x] Security scanner (scripts/security_scan.py)
- [x] Compiled binary: bin/code-graph-mcp.exe

### Bridge Layer
- [x] CodeGraphBridge class with JSON-RPC communication
- [x] Auto-detects binary location
- [x] Proper response normalization (dict/list/string)
- [x] All MCP tools exposed as Python methods:
  - analyze_impact, search, get_call_graph
  - get_ast_node, find_references, project_map
  - module_overview, trace_http_route, find_dead_code
  - dependency_graph, health_check

### Visualizer API
- [x] FastAPI server (src/trellis/api.py)
- [x] CORS enabled for web access
- [x] Endpoints:
  - GET /graph/{project_id} - Full or simplified graph
  - GET /graph/{project_id}/impact/{symbol} - Impact graph
  - GET /graph/{project_id}/search?q=... - Node search
  - GET /graph/{project_id}/node/{symbol} - Node details
  - GET /graph/{project_id}/module/{path} - Module overview
  - GET /health/{project_id} - Index status

### Smart View Modes
- [x] Automatic threshold: 200 functions
- [x] **Simplified view** (large repos): Modules + top 20 hot functions
- [x] **Full view** (small repos): All functions with relationships
- [x] Impact view: Root function + affected nodes with risk colors

### Feature Impact Layer
- [x] Parse project.md with features, decisions, constraints
- [x] Map functions to features based on file paths
- [x] Feature impact analysis with development pointers
- [x] Divergence detection from feature specs
- [x] Feature impact API endpoints

### MCP Server Integration
- [x] server.py rewritten for code-graph-mcp bridge
- [x] All 10 MCP tools working:
  - trellis_sync, trellis_search, trellis_list_features
  - trellis_analyze_impact (with feature context)
  - trellis_get_function, trellis_trace_path
  - trellis_visualize_graph, trellis_detect_hotspots
  - trellis_analyze_diff, trellis_get_boundary_map
- [x] HTTP endpoints with FastAPI:
  - GET /health, GET /graph/{project_id}
  - GET /graph/{project_id}/impact/{symbol}
  - GET /feature/{project_id}/impact/{symbol}
  - GET /feature/{project_id}/pointers/{symbol}
  - GET /feature/{project_id}/divergence/{symbol}
  - GET /spec/{project_id}, POST /spec/{project_id}
  - GET /analytics
- [x] Project path resolution (trellis → actual path)
- [x] Analytics tracking for all tools

### Testing
- [x] test_bridge_smoke.py - Import and binary detection
- [x] test_mcp_integration.py - MCP tools test
- [x] test_http_integration.py - HTTP endpoints test
- [x] test_full_integration.py - Comprehensive integration test
- [x] test_feature_impact.py - Feature parsing
- [x] test_feature_api.py - Feature impact API

## Architecture

```
trellis/
├── third_party/code-graph-mcp/    # Submodule (v0.17.3)
├── bin/code-graph-mcp.exe         # Compiled binary
├── src/trellis/
│   ├── __init__.py               # Package exports
│   ├── bridge.py                 # Python wrapper (JSON-RPC)
│   ├── feature_impact.py         # Feature impact analysis
│   └── api.py                    # FastAPI server
├── scripts/
│   ├── build_bridge.py           # Build from source
│   └── security_scan.py          # Audit submodule
├── tests/
│   └── project.md                # Sample spec for testing
├── visualizer.html               # D3.js graph UI
├── start_server.py               # Launch API server
└── SETUP.md                      # Setup instructions
```

## Current Status

**Working:**
- Binary builds from submodule
- Bridge communicates via JSON-RPC
- Health check returns index stats
- Search returns symbol matches
- Project map returns architecture
- Visualizer API formats data correctly
- **MCP server integrates with bridge** (10 tools)
- **HTTP endpoints serve correct data** (FastAPI)
- **Feature impact returns development pointers**
- **Project path resolution works** (trellis → actual path)

**Limitations:**
- Visualizer HTML still points to old endpoints (needs update)
- Call graph response format needs more parsing
- Multi-project support needs improvement (shared .code-graph dir)

## Next Steps

### Immediate (This Week)
1. [ ] Update visualizer.html to use new API endpoints
2. [ ] Test visualizer with real data
3. [ ] Test with large codebase (OmniDoc)

### Short Term (Next 2 Weeks)
4. [ ] Implement PR analyzer workflow
5. [ ] Add team coordination features
6. [ ] Create analytics dashboard for bridge usage
7. [ ] Package for pip install

### Long Term
8. [ ] Build web dashboard for team workflows
9. [ ] Add Python tooling integrations (pytest, mypy)
10. [ ] Write comprehensive documentation

## Performance

**Indexing Speed:**
- Trellis project: 37 files, 573 nodes in ~2 seconds
- Expected: 300+ files/sec (from code-graph-mcp benchmarks)

**Query Speed:**
- Search: <100ms
- Impact analysis: <200ms
- Health check: <50ms

## Security

- [x] Submodule pinned to v0.17.3
- [x] Security scan passed (no dangerous patterns)
- [x] Build from source (auditable)
- [ ] Regular update process documented

## How to Test

```bash
# 1. Start API server
python start_server.py

# 2. Open visualizer
curl http://localhost:17318

# 3. Test endpoints
curl http://localhost:17318/graph/trellis
curl http://localhost:17318/health/trellis
curl "http://localhost:17318/graph/trellis/search?q=function"

# 4. Test impact
curl http://localhost:17318/graph/trellis/impact/CodeGraphBridge
```

## Commands Summary

```bash
# Build binary
python scripts/build_bridge.py

# Security scan
python scripts/security_scan.py --skip-audit

# Start server
python start_server.py

# Run tests
python test_bridge_smoke.py
python test_bridge_e2e.py
python test_visualizer_api.py
```

## Submodule Management

```bash
# Check status
git submodule status

# Update to new version
cd third_party/code-graph-mcp
git fetch
git checkout v0.17.4
cd ../..

# Security scan + build
python scripts/security_scan.py
python scripts/build_bridge.py

# Commit update
git add third_party/code-graph-mcp bin/
git commit -m "Update code-graph-mcp to v0.17.4"
```
