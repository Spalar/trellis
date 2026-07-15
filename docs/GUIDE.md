# Trellis User Guide

## What is Trellis?

Trellis is a **dual-knowledge system** that combines code analysis with documentation to help you understand, plan, and verify changes to your codebase.

```
┌─────────────────────────────────────────────────────┐
│                    TRELLIS                          │
├──────────────────────┬──────────────────────────────┤
│     CODE GRAPH       │        DOC GRAPH             │
│  (Auto-generated)    │    (Your knowledge)          │
├──────────────────────┼──────────────────────────────┤
│  Functions           │  Feature docs                │
│  Classes             │  Architecture decisions      │
│  Call relationships  │  Knowledge notes             │
│  Import edges        │  Wiki links [[Note]]         │
│                      │  Code mentions @Function     │
├──────────────────────┴──────────────────────────────┤
│           Cross-links between code and docs          │
└─────────────────────────────────────────────────────┘
```

### Code Graph (Auto-Generated)
- **Source**: code-graph-mcp indexes your codebase
- **Contains**: Functions, methods, classes, modules, call relationships
- **Use for**: Understanding structure, tracing impact, finding hotspots

### Doc Graph (Your Knowledge)
- **Source**: Markdown notes in `.trellis/notes/`
- **Contains**: Feature specs, decisions, knowledge, divergence tracking
- **Use for**: Capturing why decisions were made, tracking technical debt, onboarding

## Quick Start

### 1. Start the Server

```bash
# Using HTTP mode (recommended for UI)
python start_server.py
# Server runs on http://localhost:17317

# Or using MCP mode (for AI agents)
python server.py
```

### 2. Index Your Repository

```bash
# Index current directory
python -c "from src.trellis import CodeGraphBridge; CodeGraphBridge('.').sync_project()"
```

### 3. Open the Visualizer

Open http://localhost:17317/ in your browser.

**Views:**
- **Code Graph**: See your codebase structure with functions, classes, and modules
- **Doc Graph**: See your knowledge notes with wiki links and backlinks

### 4. Create Knowledge Notes

In the Doc Graph view, click **+ New Note** or use the API:

```bash
curl -X POST http://localhost:17317/note/trellis/my-feature \
  -H "Content-Type: application/json" \
  -d '{"title": "My Feature", "content": "# My Feature\n\nDescription here..."}'
```

## Writing Knowledge Notes

### Wiki Links
Link between notes using double brackets:
```markdown
See [[Authentication]] for login details.
This relates to [[Decision-Use-SQLite]].
```

### Code Mentions
Reference functions/classes with @mentions:
```markdown
The @validate_auth function handles tokens.
Use @CodeGraphBridge to query the graph.
```

### Tags
Organize notes with YAML frontmatter:
```yaml
---
title: Authentication Feature
tags: feature, auth, security
---
```

### Divergence Tracking
Track where implementation diverges from specification:
```markdown
## Divergence

Current: Uses simple token validation
Target: OAuth2 with refresh tokens
Priority: High
```

## Project Specification (project.md)

Create a `project.md` file in your repo root to define features:

```markdown
## Feature: Authentication

Handles user authentication and session management.

### Decisions
- AUTH-001: Use JWT tokens (because: scales horizontally)
  - Constraint: Token expiry must be < 24 hours
  - Constraint: Refresh tokens stored in httpOnly cookies

### Files
- src/auth/**
- src/middleware/auth*

### Dependencies
- Feature: User Management

### Constraints
- All auth endpoints must return within 200ms
- Rate limiting: 5 attempts per minute
```

## API Endpoints

### Graph Data
- `GET /graph/{project_id}` - Full or simplified graph
- `GET /graph/{project_id}/impact/{symbol}` - Impact graph
- `GET /graph/{project_id}/search?q={query}` - Symbol search

### Knowledge Graph
- `GET /knowledge-graph/{project_id}` - Notes + code nodes
- `GET /note/{project_id}/{note_id}` - Get note
- `POST /note/{project_id}/{note_id}` - Save note
- `DELETE /note/{project_id}/{note_id}` - Delete note
- `GET /notes/{project_id}` - List all notes

### Feature Analysis
- `GET /feature/{project_id}/impact/{symbol}` - Feature impact report
- `GET /feature/{project_id}/context/{symbol}` - Feature context
- `GET /feature/{project_id}/pointers/{symbol}` - Development pointers
- `GET /feature/{project_id}/divergence/{symbol}` - Spec divergence check

## MCP Tools (for AI Agents)

### Code Graph Tools
| Tool | When to Use |
|------|-------------|
| `trellis_sync` | At start of session, after code changes |
| `trellis_list_modules` | Understand project directory structure |
| `trellis_search_code` | Find functions or classes by keyword |
| `trellis_get_function` | Inspect a function's signature and source |
| `trellis_module_overview` | Understand a code directory/module |
| `trellis_analyze_impact` | Before changing a function |
| `trellis_feature_info` | Get project.md feature spec + related functions |
| `trellis_trace_path` | Understand how two features or modules interact |
| `trellis_detect_hotspots` | Find high-centrality functions |
| `trellis_get_graph` | Get raw code graph data |
### Doc Graph Tools
| Tool | When to Use |
|------|-------------|
| `trellis_create_note` | Create/update knowledge note |
| `trellis_get_note` | Read full note content |
| `trellis_search_notes` | Find notes by keyword |
| `trellis_delete_note` | Remove note |
| `trellis_knowledge_graph` | Get full doc graph |

### Analysis Tools
| Tool | When to Use |
|------|-------------|
| `trellis_analyze_diff` | Before/after code changes |
| `trellis_get_boundary_map` | Identify module boundaries |

## Setup

### Prerequisites
- Python 3.11+
- Rust toolchain (for building code-graph-mcp)

### Install

```bash
# Clone with submodule
git clone --recurse-submodules https://github.com/YOUR_USERNAME/trellis.git
cd trellis

# Build code-graph-mcp binary
python scripts/build_bridge.py

# Install Python dependencies
pip install -r requirements.txt
```

### Configure

Create `.env`:
```
TRELLIS_TRANSPORT=stdio
TRELLIS_ALLOW_NO_AUTH=true
```

### Security

- All code analysis stays local
- No external API calls in production
- Optional model download is disabled by default
- Run `python scripts/security_scan.py` to audit

## Example: Impact Analysis

Before changing a function, analyze its impact:

```bash
# Technical impact
curl http://localhost:17317/graph/my-project/impact/authenticate_user

# Feature impact with development pointers
curl http://localhost:17317/feature/my-project/impact/authenticate_user

# Check for spec divergence
curl http://localhost:17317/feature/my-project/divergence/authenticate_user
```

Response includes:
- **Risk Level**: LOW/MEDIUM/HIGH
- **Affected Functions**: Count and list
- **Feature Impacts**: Which features are affected
- **Development Pointers**: Actionable guidance
- **Divergence Warnings**: Where code violates specs

## Storage

```
project/
├── .code-graph/         # Symlink to trellis data directory
│   └── index.db         # Code Graph (SQLite)
├── project.md           # Feature specifications
└── .gitignore           # .code-graph is ignored

# Trellis data directory (~/.trellis/)
.trellis/
├── projects/
│   ├── {project-id}/
│   │   ├── .code-graph/     # Code Graph (SQLite)
│   │   └── .trellis/notes/  # Doc Graph (markdown files)
│   └── ...
```
**Note**: By default, `.code-graph` data is stored in the trellis data directory (`~/.trellis/projects/{project-id}/`) to avoid polluting your project directories. A symlink or junction is created in your project directory so code-graph-mcp can find it. If symlink creation fails (e.g., on Windows without permissions), data stays in the project directory as a fallback.

**Git**: `.code-graph` is automatically added to `.gitignore` and should never be committed. It's generated data that is re-created by running `trellis_sync`.

## Tips

1. **Start with Code Graph**: Index your repo and explore the structure
2. **Add Doc Graph**: Create notes for key features and decisions
3. **Cross-link**: Use @mentions to connect docs to code
4. **Track Divergence**: Note where implementation differs from plans
5. **Analyze Before Changing**: Always run impact analysis before modifications

## License

MIT
