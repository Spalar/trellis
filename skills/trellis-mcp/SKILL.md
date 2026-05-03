---
name: "trellis-mcp"
description: "Graph-based code analysis and strategic planning using Trellis MCP tools. Use when the user wants to: (1) analyze or understand a codebase structure, (2) assess impact of proposed changes, (3) trace dependencies between features or functions, (4) plan implementations with full context of existing code, (5) explore feature graphs and identify module boundaries, (6) verify changes after implementation. Triggers: 'analyze this codebase', 'what's the impact of changing X', 'help me understand this project structure', 'before I make changes', 'trace dependencies', 'impact analysis', 'feature graph', 'understand the architecture', 'how does X relate to Y', 'plan this refactor', 'which tests should I write'.
---

# Trellis MCP Skill

Use Trellis graph tools to analyze codebases, detect impacts, and plan implementations strategically. This skill adapts proven software design principles to AI-assisted coding with graph-based context.

## When to Use Trellis

### MUST Use Trellis When:
- Starting work on an unfamiliar codebase
- About to modify any function or feature
- Refactoring or restructuring code
- Adding new features that might affect existing ones
- Reviewing code changes (PRs, commits)
- Debugging issues that span multiple files
- Planning test strategies

### DON'T Use Trellis When:
- Writing isolated utility functions with no dependencies
- Making trivial changes (comments, formatting)
- Working in a single file with no external calls
- The codebase is already fully mapped in your context

## Core Principles

### 1. Grill the AI (with Trellis)

Before asking the AI to write or modify code, use Trellis to build mental models of the codebase. Sync the repository, list features, and inspect function details. Never write code in a vacuum.

**How**: Run `trellis_sync` + `trellis_list_features` at the start of every session.

### 2. Understand Before Changing

Always analyze impact before modifying code. Trellis shows you:
- What functions call the one you're changing
- What features depend on it
- What constraints must be maintained
- What tests need updating

**How**: Run `trellis_analyze_impact` before any change.

### 3. Document as You Go

Create knowledge notes for key features and decisions. This builds institutional memory that persists across sessions.

**How**: Use `trellis_create_note` to capture decisions, architecture, and divergence tracking.

### 4. Verify After Changes

Re-sync and re-analyze after implementing changes to catch unintended side effects.

**How**: Run `trellis_sync` + `trellis_analyze_impact` after changes.

## Workflow

### Phase 1: Discovery (Always Do This First)

**Goal**: Understand the codebase structure

1. **Sync the repository**:
   ```
   trellis_sync(project_id="my-project", repo_path="/path/to/repo")
   ```
   *When: At the start of every session*

2. **List features**:
   ```
   trellis_list_features(project_id="my-project")
   ```
   *When: After sync, to see high-level structure*

3. **Search for specifics**:
   ```
   trellis_search(project_id="my-project", query="authentication")
   ```
   *When: Looking for specific functions or features*

4. **Inspect key features**:
   ```
   trellis_get_function(project_id="my-project", function_path="src/auth.py:authenticate_user")
   ```
   *When: Need to understand a specific function*

**Stop when you can answer**: "What are the 3-5 most relevant features for this task?"

### Phase 2: Strategy (Before Making Changes)

**Goal**: Plan changes safely

1. **Analyze impact**:
   ```
   trellis_analyze_impact(
     project_id="my-project",
     function_path="src/auth.py:authenticate_user",
     change_summary="Change return type from string to object"
   )
   ```
   *When: Before modifying any function*

2. **Trace critical paths**:
   ```
   trellis_trace_path(
     project_id="my-project",
     from_feature="Authentication",
     to_feature="UserManagement"
   )
   ```
   *When: Understanding how two features interact*

3. **Check module boundaries**:
   ```
   trellis_get_boundary_map(project_id="my-project")
   ```
   *When: Refactoring or extracting modules*

**Stop when you can answer**: "What could break, and what tests do I need first?"

### Phase 3: Implementation (Make Changes)

**Goal**: Implement with full context

1. **Share context with AI**:
   - Reference specific file paths and line numbers from Trellis output
   - Include constraints from impact analysis
   - Specify which functions to modify and which to leave alone

2. **Implement changes**:
   - Modify only the identified functions
   - Preserve interfaces at specific file:line locations
   - Follow constraints from project.md

3. **Update documentation**:
   ```
   trellis_create_note(
     project_id="my-project",
     note_id="decision-auth-refactor",
     title="Auth Refactor Decision",
     content="# Auth Refactor\n\nChanged authenticate_user to return object instead of string.\n\n## Impact\n- 3 callers updated\n- Feature: API Endpoints affected"
   )
   ```
   *When: Making architectural decisions*

### Phase 4: Verification (After Changes)

**Goal**: Confirm no unintended side effects

1. **Re-sync**:
   ```
   trellis_sync(project_id="my-project")
   ```
   *When: After implementing changes*

2. **Re-analyze**:
   ```
   trellis_analyze_impact(project_id="my-project", function_path="src/auth.py:authenticate_user")
   ```
   *When: Verifying changes are safe*

3. **Check divergence**:
   ```
   trellis_analyze_diff(project_id="my-project", diff="...")
   ```
   *When: Reviewing PRs or commits*

## Key Tools Reference

### Code Graph Tools

| Tool | When to Use | How Often |
|------|-------------|-----------|
| `trellis_sync` | Index or re-index codebase | Start of session, after changes |
| `trellis_list_features` | Understand project structure | Once per session |
| `trellis_search` | Find functions or features | As needed |
| `trellis_get_function` | Inspect function details | Before modifying |
| `trellis_analyze_impact` | Assess change impact | Before every change |
| `trellis_trace_path` | Trace feature dependencies | When refactoring |
| `trellis_detect_hotspots` | Find complex areas | When optimizing |
| `trellis_visualize_graph` | Get graph data | For visualization |

### Doc Graph Tools

| Tool | When to Use | How Often |
|------|-------------|-----------|
| `trellis_create_note` | Create/update knowledge | After decisions |
| `trellis_get_note` | Read full note content | When referencing docs |
| `trellis_search_notes` | Find notes by keyword | As needed |
| `trellis_delete_note` | Remove obsolete notes | During cleanup |
| `trellis_knowledge_graph` | Get full doc graph | For overview |

### Analysis Tools

| Tool | When to Use | How Often |
|------|-------------|-----------|
| `trellis_analyze_diff` | Review code changes | PR reviews |
| `trellis_get_boundary_map` | Identify module boundaries | Refactoring |

## Decision Tree

```
Starting a new task?
  → Phase 1: Discovery
    → Run: trellis_sync + trellis_list_features

About to change code?
  → Phase 2: Strategy
    → Run: trellis_analyze_impact
    → Read: references/impact-analysis.md

Changed code?
  → Phase 4: Verification
    → Run: trellis_sync + trellis_analyze_impact

Reviewing someone else's code?
  → Run: trellis_analyze_diff

Multiple approaches possible?
  → Run: trellis_trace_path to compare coupling
  → Pick the less coupled option

New domain terms appearing?
  → Run: trellis_create_note
  → Update: references/ubiquitous-language-template.md

Code diverging from docs?
  → Run: trellis_get_note on relevant feature
  → Update note with divergence section
```

## Common Patterns

### Pattern: Adding a Parameter to a Function

1. `trellis_get_function` - Check current callers
2. `trellis_analyze_impact` - Assess impact of adding parameter
3. Modify function signature
4. Update all callers (in dependency order: leaves first)
5. `trellis_sync` + `trellis_analyze_impact` - Verify

### Pattern: Extracting a Feature/Module

1. `trellis_trace_path` - Find all dependencies on extracted code
2. `trellis_get_boundary_map` - Check current boundaries
3. `trellis_analyze_feature_impact` - Assess feature-level impact
4. Create new module
5. Move code
6. Update imports
7. `trellis_sync` + verify no orphaned references

### Pattern: Refactoring for Performance

1. `trellis_detect_hotspots` - Find high-centrality functions
2. `trellis_analyze_impact` - Assess impact of optimization
3. Implement changes
4. `trellis_sync` + verify contracts preserved
5. Update notes with performance decisions

### Pattern: Onboarding to New Codebase

1. `trellis_sync` - Index the repo
2. `trellis_list_features` - See high-level structure
3. `trellis_search` - Find entry points
4. `trellis_get_function` - Understand key functions
5. `trellis_create_note` - Document learnings

## Reference Files

- **`references/impact-analysis.md`**: Detailed workflow for conducting impact analysis, choosing between function-level and feature-level tools, and interpreting results
- **`references/ubiquitous-language-template.md`**: Template for creating a `project.md` file that captures domain terminology, feature names, and architectural decisions

Read the relevant reference file before Phase 2 (Strategy) of the workflow.

## Best Practices

1. **Always sync first**: Run `trellis_sync` at the start of every session
2. **Analyze before changing**: Never modify code without running `trellis_analyze_impact`
3. **Document decisions**: Use `trellis_create_note` to capture why decisions were made
4. **Verify after changes**: Re-sync and re-analyze after implementation
5. **Track divergence**: Note when implementation differs from specification
6. **Use specific paths**: Reference functions by `file:line` from Trellis output
7. **Read references**: Check `references/impact-analysis.md` before complex changes
