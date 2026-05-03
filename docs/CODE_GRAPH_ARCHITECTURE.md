# code-graph-mcp Architecture

## Overview

code-graph-mcp is a Rust-based MCP server that indexes codebases into an AST knowledge graph. It provides semantic search, call graph analysis, and code exploration via JSON-RPC over stdio.

## Data Flow

```
Project Files
    |
    v
[File Discovery]  --> Scans for supported extensions
    |
    v
[Tree-sitter Parser] --> AST parsing (16 languages)
    |
    v
[Relation Extractor] --> Nodes (functions, classes, etc.) + Edges (calls, imports)
    |
    v
[SQLite Storage] --> .code-graph/index.db
    |   - nodes table: symbols with metadata
    |   - edges table: relationships (imports, calls)
    |   - files table: file metadata
    |
    v
[FTS5 Index] --> Full-text search on names and content
    |
    v
[Vector Embeddings] --> Optional: semantic similarity search
```

## Storage Schema

### nodes table
- `id` - Primary key
- `file_id` - Reference to files table
- `type` - Node type: function, method, class, module, etc.
- `name` - Symbol name
- `qualified_name` - Fully qualified name
- `start_line`, `end_line` - Source location
- `code_content` - Full source code
- `signature` - Function signature
- `doc_comment` - Documentation

### edges table
- `id` - Primary key
- `source_id` - Source node
- `target_id` - Target node
- `relation` - Relationship type: calls, imports, inherits, etc.
- `metadata` - Additional JSON metadata

### files table
- `id` - Primary key
- `path` - Relative file path
- `language` - Detected language
- `hash` - Content hash for change detection

## MCP Tools (7 Public)

1. **semantic_code_search** - FTS5 + vector search for concepts
2. **get_call_graph** - Multi-hop caller/callee chains
3. **get_ast_node** - Symbol details with optional references
4. **project_map** - Architecture overview with modules
5. **module_overview** - Directory/module structure
6. **ast_search** - Structural search by type/returns/params
7. **find_references** - Usage sites for rename audits

## Hidden Tools (callable by name)

- `impact_analysis` - Risk assessment for changes
- `trace_http_chain` - HTTP route tracing
- `dependency_graph` - File dependencies
- `find_dead_code` - Unused code detection
- `get_index_status` - Health check

## Key Limitations

1. **Token Truncation** - Results exceeding ~4K tokens are truncated with `_truncated: true`
2. **FTS5 Wildcards** - `search("*")` doesn't work; use `ast_search` for broad queries
3. **Edge Coverage** - Call edges may be incomplete for some languages/projects
4. **Vector Search** - Requires embedding model download (disabled in our build)

## Direct Database Access

For bypassing MCP token limits, query SQLite directly:
```python
import sqlite3
conn = sqlite3.connect('.code-graph/index.db')
```

This provides unlimited access to all 573 nodes without truncation.
