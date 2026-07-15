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

**How**: Run `trellis_sync` + `trellis_list_modules` at the start of every session.

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
   trellis_list_modules(project_id="my-project")
   ```
   *When: After sync, to see high-level directory structure*
   *Returns: List of modules with symbol counts*

3. **Search for specifics**:
   ```
   trellis_search_code(project_id="my-project", query="authenticate", limit=10)
   ```
   *When: Looking for specific functions, classes, or features*
   *Returns: Matching symbols with file paths*

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

3. **Analyze diff**:
   ```
   trellis_analyze_diff(project_id="my-project")
   ```
   *When: Reviewing PRs, commits, or uncommitted changes*
   *Returns: Changed files, affected functions, impact analysis, overall risk*
   *Parameters:*
   - `diff`: Optional raw diff string (auto-detected from git if not provided)
   - `compare_branch`: Branch to compare against (default: origin/main)

   This tool automatically:
   1. Gets diff from git (or uses provided diff)
   2. Parses changed files and line numbers
   3. Finds affected functions via code graph
   4. Runs impact analysis on each function
   5. Returns comprehensive risk report

## Key Tools Reference

### Code Graph Tools (10 tools)

| Tool | Parameters | Returns | When to Use |
|------|-----------|---------|-------------|
| `trellis_sync` | `project_id`, `repo_path`, `config_path`, `incremental` | Status, node count, file count | Start of session, after changes |
| `trellis_list_modules` | `project_id` | List of directories with symbol counts | Understand structure |
| `trellis_search_code` | `project_id`, `query`, `limit` | Matching functions/classes with paths | Find code by keyword |
| `trellis_get_function` | `project_id`, `function_path` | Function details (signature, file, line, source) | Inspect before modifying |
| `trellis_module_overview` | `project_id`, `module_path` | Module overview with symbols | Understand a code directory |
| `trellis_analyze_impact` | `project_id`, `function_path`, `depth_mode` | Risk level, affected counts, features | Before every change |
| `trellis_feature_info` | `project_id`, `feature_name` | Feature spec, functions, hot functions | Understand a project.md feature |
| `trellis_trace_path` | `project_id`, `from_feature`, `to_feature` | Dependency paths between features/modules | Trace dependencies |
| `trellis_detect_hotspots` | `project_id`, `limit` | High-centrality functions | Find complex areas |
| `trellis_get_graph` | `project_id` | Raw graph data | Visualization or analysis |

### Doc Graph Tools (5 tools)

| Tool | Parameters | Returns | When to Use |
|------|-----------|---------|-------------|
| `trellis_create_note` | `project_id`, `note_id`, `title`, `content`, `tags` | Note ID, title, links, mentions | Create/update knowledge |
| `trellis_get_note` | `project_id`, `note_id` | Full note with backlinks | Read note content |
| `trellis_search_notes` | `project_id`, `query` | Matching notes with excerpts | Find notes by keyword |
| `trellis_delete_note` | `project_id`, `note_id` | Status message | Remove obsolete notes |
| `trellis_knowledge_graph` | `project_id` | Full graph with notes + code nodes | Get overview |

### Analysis Tools (2 tools)

| Tool | Parameters | Returns | When to Use |
|------|-----------|---------|-------------|
| `trellis_get_boundary_map` | `project_id` | Module boundary map | Identify boundaries |
| `trellis_analyze_diff` | `project_id`, `diff`, `compare_branch` | Changed files, affected functions, impact report, risk level | PR reviews, pre-commit checks |

### Tool Parameter Details

**trellis_list_modules**:
- `project_id`: Project identifier
- Returns: Directory modules with file and symbol counts

**trellis_search_code**:
- `project_id`: Project identifier
- `query`: Keyword to search for in code symbols
- `limit`: Max results (default: 10)

**trellis_module_overview**:
- `project_id`: Project identifier
- `module_path`: Directory path or module name
- Returns: Symbols and files in that module

**trellis_feature_info**:
- `project_id`: Project identifier
- `feature_name`: Feature name from project.md (e.g. "Icons", "Authentication")
- Returns: Spec, decisions, constraints, functions, hot functions, related notes

**trellis_get_function**:
- `function_path`: Function name, qualified name, or file:function format

**trellis_analyze_impact**:
- `function_path`: Function name or qualified name (e.g., "authenticate_user" or "auth.authenticate_user")
- `depth_mode`: "standard" or "deep" for analysis depth

**trellis_get_graph**:
- `project_id`: Project identifier
- Returns: Raw code graph nodes and edges (for visualization or custom analysis)

**trellis_trace_path**:
- `from_feature`: Source feature or module (can be project.md feature name)
- `to_feature`: Target feature or module
- Returns: Direct and indirect call/import edges

**trellis_detect_hotspots**:
- `project_id`: Project identifier
- `limit`: Number of hotspots to return (default: 20)
- Returns: Functions with the most incoming calls/imports

**trellis_analyze_diff**:
- `diff`: Optional raw diff string. If not provided, automatically fetches from git working tree
- `compare_branch`: Branch to compare against (default: origin/main or main)
- Returns: Overall risk, changed files, affected functions with impact analysis

**trellis_create_note**:
- `note_id`: Unique identifier (e.g., "feature-auth", "decision-jwt")
- `title`: Human-readable title
- `content`: Markdown content with `[[links]]` and `@mentions`
- `tags`: Comma-separated tags (optional)

## Decision Tree

```
Starting a new task?
  → Run: trellis_sync + trellis_list_modules

About to change code?
  → Phase 2: Strategy
    → Run: trellis_analyze_impact

Changed code?
  → Phase 4: Verification
    → Run: trellis_sync + trellis_analyze_impact

Reviewing someone else's code?
  → Run: trellis_analyze_diff (auto-detects git changes and analyzes impact)

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

### Pattern: Reviewing Code Changes

1. `trellis_analyze_diff` - Auto-detect git changes and get impact report
2. Focus on HIGH/CRITICAL risk functions
3. `trellis_get_function` - Inspect specific functions if needed
4. `trellis_analyze_impact` - Deep dive on high-risk changes

### Pattern: Onboarding to New Codebase

1. `trellis_sync` - Index the repo
2. `trellis_list_modules` - See high-level directory structure
3. `trellis_search_code` - Find entry points
4. `trellis_get_function` - Understand key functions
5. `trellis_feature_info` - Understand project.md features
6. `trellis_create_note` - Document learnings

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
9. **Re-sync if call edges seem missing**: If impact analysis returns 0 callers unexpectedly, run `trellis_sync` to rebuild the code graph (code-graph-mcp background indexing may wipe custom call edges)
