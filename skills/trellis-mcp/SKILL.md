---
name: "trellis-mcp"
description: "Graph-based code analysis and knowledge management using Trellis MCP tools. Use when the user wants to: (1) analyze or understand a codebase structure, (2) assess impact of proposed changes, (3) trace dependencies between features or functions, (4) plan implementations with full context, (5) create or query linkable documentation, (6) verify changes after implementation. Triggers: 'analyze this codebase', 'what's the impact of changing X', 'help me understand this project', 'before I make changes', 'trace dependencies', 'impact analysis', 'feature graph', 'understand the architecture', 'how does X relate to Y', 'plan this refactor', 'create a note about', 'find documentation about'.
---

# Trellis MCP Skill

Use Trellis tools to analyze codebases and manage linkable documentation. Trellis provides two graph views:
- **Code Graph**: Auto-generated from your codebase (functions, classes, calls)
- **Doc Graph**: Your knowledge notes with wiki links `[[Note]]` and code mentions `@Function`

## When to Use Trellis

### MUST Use Trellis When:
- Starting work on an unfamiliar codebase
- About to modify any function or feature
- Refactoring or restructuring code
- Adding new features that might affect existing ones
- Need to document architecture decisions or feature specs
- Want to track divergence between docs and implementation
- Debugging issues that span multiple files

### DON'T Use Trellis When:
- Writing isolated utility functions with no dependencies
- Making trivial changes (comments, formatting)
- Working in a single file with no external calls
- The codebase is already fully mapped in your context

## Core Principles

### 1. Discovery First

Before writing or modifying code, use Trellis to build mental models of the codebase. Sync the repository, list features, and inspect function details. Never write code in a vacuum.

**How**: Run `trellis_sync` + `trellis_list_features` at the start of every session.

### 2. Analyze Before Changing

Always run impact analysis before modifying code. Trellis shows you:
- What functions call the one you're changing
- What features depend on it
- Risk level (LOW/MEDIUM/HIGH)
- Affected function count and file count

**How**: Run `trellis_analyze_impact` before any change.

### 3. Document as You Go

Create knowledge notes for key features, decisions, and divergence tracking. Notes support:
- Wiki links: `[[Other Note]]` for connecting ideas
- Code mentions: `@function_name` for referencing code
- Bidirectional backlinks (auto-computed)
- YAML frontmatter tags

**How**: Use `trellis_create_note` to capture knowledge.

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
   *What it does: Indexes the codebase into the code graph*

2. **List features/modules**:
   ```
   trellis_list_features(project_id="my-project")
   ```
   *When: After sync, to see high-level structure*
   *Returns: List of modules with symbol counts*

3. **Search for specifics**:
   ```
   trellis_search(project_id="my-project", query="authenticate", limit=10)
   ```
   *When: Looking for specific functions or features*
   *Returns: Matching functions with file paths and scores*

4. **Inspect key functions**:
   ```
   trellis_get_function(project_id="my-project", function_path="authenticate_user")
   ```
   *When: Need to understand a specific function*
   *Returns: Function details including signature, file path, line numbers*

**Stop when you can answer**: "What are the 3-5 most relevant features for this task?"

### Phase 2: Strategy (Before Making Changes)

**Goal**: Plan changes safely

1. **Analyze impact**:
   ```
   trellis_analyze_impact(
     project_id="my-project",
     function_path="authenticate_user",
     depth_mode="standard"
   )
   ```
   *When: Before modifying any function*
   *Returns: Risk level, affected functions count, affected files count, feature impacts*
   *Parameters:*
   - `function_path`: Function name or qualified name
   - `depth_mode`: "standard" or "deep" (default: "standard")

2. **Check module boundaries**:
   ```
   trellis_get_boundary_map(project_id="my-project")
   ```
   *When: Refactoring or extracting modules*
   *Returns: Module dependency map with boundary crossings*

3. **Find hotspots**:
   ```
   trellis_detect_hotspots(project_id="my-project")
   ```
   *When: Optimizing or identifying complex areas*
   *Returns: High-centrality functions (most referenced)*

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

3. **Document decisions**:
   ```
   trellis_create_note(
     project_id="my-project",
     note_id="decision-auth-refactor",
     title="Auth Refactor Decision",
     content="# Auth Refactor\n\nChanged authenticate_user to return object instead of string.\n\n## Impact\n- 3 callers updated\n- Feature: API Endpoints affected",
     tags="decision, auth, refactor"
   )
   ```
   *When: Making architectural decisions*
   *Note: `tags` is optional, comma-separated string*

### Phase 4: Verification (After Changes)

**Goal**: Confirm no unintended side effects

1. **Re-sync**:
   ```
   trellis_sync(project_id="my-project")
   ```
   *When: After implementing changes*

2. **Re-analyze**:
   ```
   trellis_analyze_impact(project_id="my-project", function_path="authenticate_user")
   ```
   *When: Verifying changes are safe*

3. **Check divergence**:
   ```
   trellis_analyze_diff(project_id="my-project", diff="...")
   ```
   *When: Reviewing PRs or commits*
   *Note: Currently simplified; use trellis_analyze_impact for detailed analysis*

## Key Tools Reference

### Code Graph Tools (10 tools)

| Tool | Parameters | Returns | When to Use |
|------|-----------|---------|-------------|
| `trellis_sync` | `project_id`, `repo_path`, `config_path`, `incremental` | Status, node count, file count | Start of session, after changes |
| `trellis_list_features` | `project_id` | List of modules with symbol counts | Understand structure |
| `trellis_search` | `project_id`, `query`, `limit` | Matching functions with paths | Find functions by concept |
| `trellis_get_function` | `project_id`, `function_path` | Function details (signature, file, line) | Inspect before modifying |
| `trellis_get_feature` | `project_id`, `feature_name` | Module overview with symbols | Understand a module |
| `trellis_analyze_impact` | `project_id`, `function_path`, `depth_mode` | Risk level, affected counts, features | Before every change |
| `trellis_trace_path` | `project_id`, `from_feature`, `to_feature` | Dependency path between features | Trace dependencies |
| `trellis_detect_hotspots` | `project_id` | High-centrality functions | Find complex areas |
| `trellis_visualize_graph` | `project_id` | Graph data for UI | Visualization |
| `trellis_analyze_diff` | `project_id`, `diff` | Diff analysis summary | PR reviews |

### Doc Graph Tools (6 tools)

| Tool | Parameters | Returns | When to Use |
|------|-----------|---------|-------------|
| `trellis_create_note` | `project_id`, `note_id`, `title`, `content`, `tags` | Note ID, title, links, mentions | Create/update knowledge |
| `trellis_get_note` | `project_id`, `note_id` | Full note with backlinks | Read note content |
| `trellis_search_notes` | `project_id`, `query` | Matching notes with excerpts | Find notes by keyword |
| `trellis_delete_note` | `project_id`, `note_id` | Status message | Remove obsolete notes |
| `trellis_knowledge_graph` | `project_id` | Full graph with notes + code nodes | Get overview |
| `trellis_get_boundary_map` | `project_id` | Module boundary map | Identify boundaries |

### Tool Parameter Details

**trellis_sync**:
- `project_id`: Project identifier (used for all subsequent calls)
- `repo_path`: Path to repository (optional, defaults to project_id)
- `config_path`: Config file path (default: ".trellis/config.yaml")
- `incremental`: Only index changed files (default: false)

**trellis_analyze_impact**:
- `function_path`: Function name or qualified name (e.g., "authenticate_user" or "auth.authenticate_user")
- `depth_mode`: "standard" or "deep" for analysis depth

**trellis_create_note**:
- `note_id`: Unique identifier (e.g., "feature-auth", "decision-jwt")
- `title`: Human-readable title
- `content`: Markdown content with `[[links]]` and `@mentions`
- `tags`: Comma-separated tags (optional)

## Decision Tree

```
Starting a new task?
  → Phase 1: Discovery
    → Run: trellis_sync + trellis_list_features

About to change code?
  → Phase 2: Strategy
    → Run: trellis_analyze_impact

Changed code?
  → Phase 4: Verification
    → Run: trellis_sync + trellis_analyze_impact

Reviewing someone else's code?
  → Run: trellis_analyze_diff (or trellis_analyze_impact on key functions)

Multiple approaches possible?
  → Run: trellis_trace_path to compare coupling
  → Pick the less coupled option

Need to document something?
  → Run: trellis_create_note
  → Link related notes with [[Note]] and code with @Function

Code diverging from docs?
  → Run: trellis_get_note on relevant feature
  → Update note with divergence section

Looking for documentation?
  → Run: trellis_search_notes
  → Or: trellis_knowledge_graph for full overview
```

## Common Patterns

### Pattern: Adding a Parameter to a Function

1. `trellis_get_function` - Check current signature and callers
2. `trellis_analyze_impact` - Assess impact of adding parameter
3. Modify function signature
4. Update all callers (in dependency order: leaves first)
5. `trellis_sync` + `trellis_analyze_impact` - Verify

### Pattern: Extracting a Feature/Module

1. `trellis_trace_path` - Find all dependencies on extracted code
2. `trellis_get_boundary_map` - Check current boundaries
3. `trellis_analyze_impact` on key functions - Assess impact
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

### Pattern: Documenting Architecture Decisions

1. `trellis_create_note` with note_id="decision-{topic}"
2. Include context, decision, consequences
3. Link related features with `[[Feature-Name]]`
4. Reference code with `@function_name`
5. Add divergence section if implementation differs

## Reference Files

- **`references/impact-analysis.md`**: Detailed workflow for conducting impact analysis and interpreting results
- **`references/ubiquitous-language-template.md`**: Template for creating a `project.md` file that captures domain terminology and architectural decisions

Read the relevant reference file before Phase 2 (Strategy) of the workflow.

## Best Practices

1. **Always sync first**: Run `trellis_sync` at the start of every session
2. **Analyze before changing**: Never modify code without running `trellis_analyze_impact`
3. **Document decisions**: Use `trellis_create_note` to capture why decisions were made
4. **Verify after changes**: Re-sync and re-analyze after implementation
5. **Track divergence**: Note when implementation differs from specification
6. **Use specific paths**: Reference functions by name from Trellis output
7. **Link everything**: Use `[[Note]]` and `@Function` to connect docs and code
8. **Read references**: Check `references/impact-analysis.md` before complex changes
