# Trellis Project Specification

## Overview

Trellis is a dual-knowledge system combining Code Graph (from code-graph-mcp) with Doc Graph (linkable markdown notes) for unified impact analysis and divergence tracking.

## Feature: Code Graph Bridge

Integration layer with code-graph-mcp for technical code analysis.

### Decisions
- BRIDGE-001: Use JSON-RPC for MCP communication (because: standard protocol)
  - Constraint: Must handle process lifecycle (spawn/kill per stdio call)
  - Constraint: Cache bridge instances for HTTP mode only
- BRIDGE-002: SQLite direct queries for full graph (because: MCP has ~4K token limits)
  - Constraint: Bypass MCP for large datasets
  - Constraint: Must maintain compatibility with MCP schema

### Files
- src/trellis/bridge.py
- src/trellis/api.py

### Dependencies
- code-graph-mcp (external)

## Feature: Feature Impact Analysis

Analyzes how code changes impact feature-level decisions and constraints.

### Decisions
- IMPACT-001: Parse project.md for feature specs (because: no LLM API available)
  - Constraint: Must follow strict markdown format
  - Constraint: File patterns use glob syntax
- IMPACT-002: Combine technical + feature impact (because: dual perspective needed)
  - Constraint: Technical impact from code-graph-mcp
  - Constraint: Feature context from project.md

### Files
- src/trellis/feature_impact.py
- src/trellis/impact_analyzer.py
- src/trellis/python_call_indexer.py
- tests/project.md

### Dependencies
- Feature: Code Graph Bridge

## Feature: Knowledge Graph

Linkable markdown notes with wiki links and bidirectional backlinks.

### Decisions
- KG-001: Store notes as markdown files (because: human-readable, git-friendly)
  - Constraint: Must support wiki links [[Note]]
  - Constraint: Must support code mentions @Function
- KG-002: Bidirectional backlinks (because: discoverability)
  - Constraint: Auto-compute on note read
  - Constraint: Cross-link with code graph

### Files
- src/trellis/knowledge_graph.py
- .trellis/notes/**

### Dependencies
- Feature: Code Graph Bridge

## Feature: MCP Server

Provides 16 MCP tools for code analysis and knowledge graph operations.

### Decisions
- SERVER-001: Support both stdio and HTTP transports (because: flexibility)
  - Constraint: stdio spawns process per call (500ms-2s overhead)
  - Constraint: HTTP stays resident with cached bridge
- SERVER-002: Unified API via FastMCP (because: single framework)
  - Constraint: Must support custom routes
  - Constraint: Tool responses must be JSON strings

### Files
- server.py
- src/trellis/api.py

### Dependencies
- Feature: Code Graph Bridge
- Feature: Feature Impact Analysis
- Feature: Knowledge Graph

## Feature: Visualizer

Clean web UI for exploring code and doc graphs.

### Decisions
- UI-001: Clean UI design (because: familiar, low cognitive load)
  - Constraint: Use ui-monospace font
  - Constraint: Split pane (editor + graph)
- UI-002: Dual graph views (because: separate concerns)
  - Constraint: Code Graph for structure
  - Constraint: Doc Graph for knowledge

### Files
- visualizer.html
- analytics.html

### Dependencies
- Feature: MCP Server

## Feature: Security & Analytics

Security scanning and usage analytics.

### Decisions
- SEC-001: Optional auth for air-gapped use (because: development environments)
  - Constraint: TRELLIS_ALLOW_NO_AUTH env var

### Files
- scripts/security_scan.py
- auth.py
- analytics.py

### Constraints
- Must not log secrets or keys
- Must validate all inputs
