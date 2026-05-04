# Trellis - Feature Impact Analysis for Code

Trellis is a dual-knowledge system that combines **code graph analysis** with **documentation** to help you understand, plan, and verify changes to your codebase.

## What It Does

### Technical Impact
- "This function calls 5 other functions"
- "Risk level: HIGH"

### Feature Impact
- "This change affects the Authentication feature"
- "Decision AUTH-001 says use JWT tokens - are you maintaining that?"
- "Constraint: Token expiry must be < 24 hours"

### Linkable Docs
- Write markdown notes that link to each other with `[[Note]]`
- Reference code directly with `@Function` mentions
- Track where implementation diverges from plans
- Bidirectional backlinks show related notes automatically

## Quick Start

```bash
# Start server
python start_server.py

# Open visualizer
open http://localhost:17317
```

## Documentation

- **[User Guide](docs/GUIDE.md)** - Setup, usage, writing notes, API reference
- **[Architecture](docs/ARCHITECTURE.md)** - Technical details, migration, security

## Key Features

- **Code Graph**: Auto-generated from your codebase (functions, classes, calls)
- **Doc Graph**: Your knowledge notes with cross-links to code
- **Impact Analysis**: See what breaks before you change code
- **Divergence Detection**: Warns when code violates feature specs
- **Web UI**: Clean visualizer with interactive graph
- **MCP Tools**: 16 tools for AI coding agents

## Project Structure

```
trellis/
├── docs/              # Documentation
├── src/trellis/       # Core Python code
├── visualizer.html    # Web UI
├── server.py          # MCP + HTTP server
└── project.md         # Feature specifications
```

## License

MIT
