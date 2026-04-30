# Trellis - Feature Impact Analysis for Code

Trellis is a Python-native code graph workflow layer built on top of **code-graph-mcp**.

It provides **feature-level impact analysis** on top of technical code graph analysis, helping coding agents understand not just *what* changed, but *why* decisions were made and *what constraints* must be maintained.

## What It Does

### Technical Impact (from code-graph-mcp)
- "This function calls 5 other functions"
- "Risk level: HIGH"

### Feature Impact (from Trellis)
- "This change affects the Authentication feature"
- "Decision AUTH-001 says use JWT tokens - are you maintaining that?"
- "Constraint: Token expiry must be < 24 hours"
- "Dependency: User Management depends on this"

## Quick Start

### 1. Start the Server

```bash
python start_server.py
```

Server runs on http://localhost:17318

### 2. Index Your Repo

```bash
cd your-project
path/to/trellis/bin/code-graph-mcp.exe rebuild-index --confirm
```

### 3. Query the API

```bash
# Health check
curl http://localhost:17318/health/your-project

# Full graph (auto-simplified if >200 functions)
curl http://localhost:17318/graph/your-project

# Impact analysis
curl http://localhost:17318/graph/your-project/impact/authenticate_user

# Feature impact with development pointers
curl http://localhost:17318/feature/your-project/impact/authenticate_user

# Development pointers for coding agent
curl http://localhost:17318/feature/your-project/pointers/authenticate_user

# Check for spec divergence
curl http://localhost:17318/feature/your-project/divergence/authenticate_user
```

### 4. Open Visualizer

Open `visualizer.html` in your browser or visit http://localhost:17318/

## Architecture

```
Trellis
├── code-graph-mcp (Rust submodule)
│   ├── AST parsing (16 languages)
│   ├── Call graph analysis
│   ├── Impact analysis
│   └── JSON-RPC API
│
├── Trellis Bridge (Python)
│   ├── CodeGraphBridge - JSON-RPC wrapper
│   ├── FeatureImpactAnalyzer - Feature context
│   └── FastAPI server - REST endpoints
│
└── project.md - Feature specifications
    ├── Feature definitions
    ├── Decisions with rationale
    ├── Constraints
    └── File patterns
```

## API Endpoints

### Graph Endpoints
- `GET /graph/{project_id}` - Full or simplified graph
- `GET /graph/{project_id}/impact/{symbol}` - Impact graph
- `GET /graph/{project_id}/search?q={query}` - Symbol search
- `GET /graph/{project_id}/node/{symbol}` - Node details
- `GET /graph/{project_id}/module/{path}` - Module overview

### Feature Endpoints
- `GET /feature/{project_id}/impact/{symbol}` - Feature impact report
- `GET /feature/{project_id}/context/{symbol}` - Feature context
- `GET /feature/{project_id}/pointers/{symbol}` - Development pointers
- `GET /feature/{project_id}/divergence/{symbol}` - Spec divergence check

### Health
- `GET /health/{project_id}` - Index status

## Python Usage

```python
from src.trellis import CodeGraphBridge

# Initialize for a project
bridge = CodeGraphBridge("/path/to/your/repo")

# Get feature impact report
report = bridge.get_feature_impact("authenticate_user")

# Print development pointers for coding agent
for pointer in report['development_pointers']:
    print(f"- {pointer}")

# Check for divergence from spec
warnings = bridge.check_feature_divergence("authenticate_user")
for w in warnings:
    print(f"⚠️ {w}")

# Get technical impact only
impact = bridge.analyze_impact("authenticate_user")
print(f"Risk: {impact['risk_level']}")

# Search symbols
results = bridge.search("auth", limit=10)
```

## project.md Format

```markdown
## Feature: Authentication

Handles user authentication and session management.

### Decisions
- AUTH-001: Use JWT tokens (because: scales horizontally)
  - Constraint: Token expiry must be < 24 hours
  - Constraint: Refresh tokens stored in httpOnly cookies
- AUTH-002: Password hashing with bcrypt (because: industry standard)
  - Constraint: Minimum 12 rounds

### Files
- src/auth/**
- src/middleware/auth*

### Dependencies
- Feature: User Management

### Constraints
- All auth endpoints must return within 200ms
- Rate limiting: 5 attempts per minute
```

## Setup

### Prerequisites
- Python 3.11+
- Rust (for building from source) or Node.js (for npm install)

### Install

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/trellis.git
cd trellis

# Add submodule (forked code-graph-mcp)
git submodule update --init

# Build binary
python scripts/build_bridge.py

# Install Python deps
pip install -r requirements.txt
```

### Configure

Create `.env`:
```
TRELLIS_TRANSPORT=stdio
TRELLIS_ALLOW_NO_AUTH=true
```

## Configuration Files

- `.env` - Environment variables
- `opencode.json` - MCP client configuration
- `project.md` - Feature specifications

## Project Structure

```
trellis/
├── bin/                    # Compiled binary (auto-built)
├── scripts/
│   ├── build_bridge.py     # Build from source
│   └── security_scan.py    # Audit submodule
├── src/trellis/
│   ├── bridge.py           # code-graph-mcp wrapper
│   ├── feature_impact.py   # Feature analysis
│   └── api.py              # FastAPI server
├── tests/
│   └── project.md          # Sample spec
├── third_party/
│   └── code-graph-mcp/     # Forked submodule
├── visualizer.html         # Interactive graph UI
├── start_server.py         # Launch script
└── SETUP.md               # Detailed setup guide
```

## Key Features

- **Smart View Modes**: Full detail (<200 functions) or simplified overview (>200)
- **Feature Impact**: Combines technical + feature-level analysis
- **Development Pointers**: Actionable guidance for coding agents
- **Divergence Detection**: Warns when code violates feature specs
- **Security**: Build from source, audit submodule, pin versions

## Performance

- Indexing: 300+ files/sec (code-graph-mcp)
- Search: <100ms
- Impact analysis: <200ms
- Health check: <50ms

## Security

- Submodule pinned to v0.17.3
- Build from source (auditable)
- Security scan: `python scripts/security_scan.py`
- Forked repo (not dependent on upstream)

## Documentation

- `SETUP.md` - Setup instructions
- `MIGRATION.md` - Migrating old code
- `FEATURE_IMPACT_EXAMPLE.md` - Feature impact examples
- `PIVOT_PROGRESS.md` - Development progress

## License

MIT
