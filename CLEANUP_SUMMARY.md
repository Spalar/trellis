# Cleaned Repository Structure

## Removed Files (24 files)
- extractor.py, engine.py, impact_analyzer.py, store.py
- models.py, feature_intent.py, router.py
- visualizer.py, ast_cache.py
- test_bridge_*.py, test_feature_*.py, test_view_modes.py
- tests/conftest.py, tests/test_engine.py, tests/test_impact_analysis.py
- tests/test_server.py, tests/test_token_efficiency.py
- REVIEW.md, REVIEW_POST_FIX.md

## Remaining Structure

```
trellis/
├── bin/                          # Compiled binary (gitignored)
│   ├── code-graph-mcp.exe
│   └── version.txt
├── scripts/                      # Build & security
│   ├── build_bridge.py
│   └── security_scan.py
├── skills/                       # OpenCode skills
│   ├── clean-code/
│   └── trellis-mcp/
├── src/trellis/                  # Core Python code
│   ├── __init__.py
│   ├── bridge.py                 # code-graph-mcp wrapper
│   ├── feature_impact.py         # Feature analysis layer
│   └── api.py                    # FastAPI server
├── tests/
│   └── project.md                # Sample spec
├── third_party/                  # Submodule
│   └── code-graph-mcp/           # Forked repo
├── analytics.html                # Analytics dashboard
├── analytics.py                  # Analytics backend
├── auth.py                       # Auth logic
├── Dockerfile
├── docker-compose.yml
├── LICENSE
├── Makefile
├── README.md
├── requirements.txt
├── pyproject.toml
├── server.py                     # MCP server (keep?)
├── spec_manager.py               # Spec management (keep?)
├── start_server.py               # Launch script
├── visualizer.html               # Web UI (needs update)
├── .env                          # Environment
├── .env.example
├── opencode.json                 # MCP config
├── opencode.json.example
├── .gitignore
├── .gitmodules
├── SETUP.md
├── MIGRATION.md
├── PIVOT_PROGRESS.md
└── FEATURE_IMPACT_EXAMPLE.md
```

## Key Decisions

**Kept (still useful):**
- server.py - MCP server endpoint handlers
- auth.py - Authentication logic
- spec_manager.py - project.md read/write
- analytics.py - Usage tracking

**Could be removed later:**
- server.py (if fully replaced by api.py)
- spec_manager.py (if replaced by feature_impact.py parser)
- analytics.py (if not used)

**Needs updating:**
- visualizer.html - Point to new API endpoints
- server.py - Integrate with bridge
- README.md - Update documentation
