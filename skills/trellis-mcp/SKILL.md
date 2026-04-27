---
name: "trellis-mcp"
description: "Graph-based code analysis and strategic planning using Trellis MCP tools. Use when the user wants to: (1) analyze or understand a codebase structure, (2) assess impact of proposed changes, (3) trace dependencies between features or functions, (4) plan implementations with full context of existing code, (5) explore feature graphs and identify module boundaries, (6) verify changes after implementation. Triggers: 'analyze this codebase', 'what's the impact of changing X', 'help me understand this project structure', 'before I make changes', 'trace dependencies', 'impact analysis', 'feature graph', 'understand the architecture', 'how does X relate to Y', 'plan this refactor', 'which tests should I write'."
---

# Trellis MCP Skill

Use Trellis graph tools to analyze codebases, detect impacts, and plan implementations strategically. This skill adapts proven software design principles to AI-assisted coding with graph-based context.

## Core Principles

### Grill the AI (with Trellis)

Before asking the AI to write or modify code, use Trellis to build mental models of the codebase. Sync the repository, list features, and inspect function details. Never write code in a vacuum.

### Ubiquitous Language

Create a shared vocabulary between human, AI, and codebase. Capture domain terms, feature names, and conventions in a `project.md` file using the template in `references/ubiquitous-language-template.md`. Reference this file in all planning conversations.

### Test-Driven Impact Analysis

Use impact analysis to determine what tests to write first. Before modifying code, analyze which features and functions are affected. Write tests for impacted areas before implementation.

### Deep Modules via Feature Graphs

Use `trellis_list_features` and `trellis_get_feature` to identify natural module boundaries. Look for features with high internal cohesion and low external coupling. Extract deep modules where the interface is simple but the implementation is complex.

### Design Interfaces, Delegate Implementation

Use Trellis to understand existing interfaces and design new ones. Trace paths between features to understand contracts. Design the interface first, then delegate implementation details to the AI with full context of callers and callees.

## Workflow

### Phase 1: Discovery

1. Sync the repository: `trellis_sync` with the repo path and project ID
2. List features: `trellis_list_features` to see the high-level structure
3. Inspect key features: `trellis_get_feature` on areas relevant to the task
4. Search for specifics: `trellis_search` to find functions or features by keyword

Stop when you can answer: "What are the 3-5 most relevant features for this task?"

### Phase 2: Strategy

1. Analyze impact of proposed changes:
   - For function-level changes: `trellis_analyze_impact` on the specific function
   - For feature-level changes: `trellis_analyze_feature_impact` on the feature
2. Trace critical paths: `trellis_trace_path` between features that interact
3. Design interfaces using dependency data from `trellis_get_function` with callers/callees
4. Document decisions in `project.md` using the ubiquitous language template

Stop when you can answer: "What could break, and what tests do I need first?"

### Phase 3: Implementation

1. Share relevant feature and function context with the AI
2. Reference specific file paths and line numbers from Trellis output
3. Ask the AI to implement with constraints: "Only modify these 3 functions, preserve the interface at file:line"
4. Update `project.md` if new domain terms emerge

### Phase 4: Verification

1. Re-sync the repository: `trellis_sync` to capture changes
2. Re-run impact analysis on modified functions to validate no unintended side effects
3. Use `trellis_get_function` to verify callers still have correct contracts
4. Update feature graphs if module boundaries changed

## Key Tools Reference

| Tool | When to Use |
|------|-------------|
| `trellis_sync` | At start of every session and after implementing changes |
| `trellis_list_features` | To understand project structure and find entry points |
| `trellis_get_feature` | To understand a specific feature's implementation and neighbors |
| `trellis_search` | To find functions or features by name or keyword |
| `trellis_get_function` | To inspect a function's callers, callees, and implementation |
| `trellis_trace_path` | To understand how two features interact through the graph |
| `trellis_analyze_impact` | Before changing a function to see downstream effects |
| `trellis_analyze_feature_impact` | Before changing a feature to see cross-feature effects |
| `trellis_visualize_graph` | When you need to share or explore the graph visually |

## Reference Files

- **`references/impact-analysis.md`**: Detailed workflow for conducting impact analysis, choosing between function-level and feature-level tools, and interpreting results
- **`references/ubiquitous-language-template.md`**: Template for creating a `project.md` file that captures domain terminology, feature names, and architectural decisions

Read the relevant reference file before Phase 2 (Strategy) of the workflow.

## Quick Decision Tree

```
Need to understand structure?
  -> Phase 1: Discovery

About to change something?
  -> Phase 2: Strategy (read impact-analysis.md)

Changed something?
  -> Phase 4: Verification

Multiple valid approaches exist?
  -> Use trace_path to compare coupling, pick the less coupled option

New domain terms appearing?
  -> Update ubiquitous-language-template.md
```
