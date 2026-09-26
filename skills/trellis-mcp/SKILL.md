---
name: "trellis-mcp"
description: "Graph-based code analysis and knowledge management using Trellis MCP tools. Use when the user wants to: (1) analyze or understand a codebase structure, (2) assess impact of proposed changes, (3) trace dependencies between features or functions, (4) plan implementations with full context, (5) create or query linkable documentation, (6) verify changes after implementation. Triggers: analyze this codebase, what's the impact of changing X, help me understand this project, before I make changes, trace dependencies, impact analysis, feature graph, understand the architecture, how does X relate to Y, plan this refactor, create a note about, find documentation about."
---

# Trellis MCP Skill

Use Trellis tools to analyze codebases and manage linkable documentation. Trellis provides two graph views:
- **Code Graph**: Auto-generated from your codebase (functions, classes, calls)
- **Doc Graph**: Your knowledge notes with wiki links `[[Note]]` and code mentions `@Function`

## Don't Have This Skill Installed?

You can still use Trellis effectively — the MCP server bootstraps you:
1. This server's MCP `instructions` (sent at connect) contain the essential getting-started workflow — read them first.
2. Read the resource `trellis://skill` to fetch the full content of this skill document, then follow it.
3. Then start with Phase 1 below (`trellis_sync` + `trellis_list_modules`).

To install the skill permanently, copy the `trellis-mcp` skill folder (bundled with the Trellis release/repo under `skills/`) into your agent's skills directory.

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

## Project.md (Feature Specification) — MANDATORY

Trellis reads `project.md` in your **repo root** to understand feature boundaries. **If `project.md` is missing, you MUST create it before continuing with feature-level work.** Feature tools (`trellis_feature_info`, `trellis_trace_path`, feature impacts in `trellis_analyze_impact`) are driven entirely by this file — without it, functions are reported as "not mapped to any feature". Every `trellis_sync` response includes a `project_md` field (`ok` or `missing` + instructions); treat `missing` as your next mandatory step.

### The exact format Trellis parses

Trellis parses a strict structure. Other layouts (tables, `- **Description**:` bullet props) are silently ignored — features written that way are invisible to feature analysis. One section per feature:

```markdown
# Project Name

## Feature: Icons

Renders and manages toolbar icons. Prefer plain sentences here — this paragraph is the feature description.

### Decisions
- ICON-001: SVG icons are preferred over font icons (because: accessibility)
  - Constraint: New icons must ship with a11y labels

### Files
- src/js/component/icon*
- src/js/component/iconRegistry.js

### Dependencies
- Feature: Graphics
```

Rules the parser follows:
- `## Feature: <Name>` — the name after the colon is the exact name you query with `trellis_feature_info` and `trellis_trace_path`. No code fences or bold inside the heading.
- **Description** = plain text lines directly under the heading, before any `###` section. Bullet lines (`- ...`) are NOT treated as description.
- `### Decisions` entries use `- DEC-001: decision text (because: rationale)`; nested `- Constraint: ...` lines attach constraints to the decision above them. Entries without a `XXX-000` ID still work — Trellis assigns a `GEN-###` ID.
- `### Files` takes one glob pattern per line (`src/auth/**`, `src/middleware/auth*`). These patterns map source files to features — without them the feature has no functions. Use forward slashes.
- `### Dependencies` takes one feature name per line; the `Feature:` prefix is optional and stripped.

Flexibility — project.md is also your project document:
- Free-form sections anywhere are fine. Any `##` heading that is not a feature (e.g. `## Overview`, `## How It Works`, `## Roadmap`) is ignored by the parser, so you can document the project freely between or around feature sections.
- As a shortcut, feature fields may be written as bullet props: `- **Description**: ...`, `- **Decisions**: ...`, `- **Files**: a, b`, `- **Dependencies**: X, Y`. The section format above is preferred, but both parse.

### What to do with it (workflow)

1. **After your first `trellis_sync`**: check the `project_md` field. If `missing`, create `project.md` at the repo root now, before any feature-level work.
2. **Populate it from discovery output**: derive feature names from `trellis_list_modules` / `trellis_search_code` results; derive `### Files` globs from the file paths those tools return. Start with 3-5 coarse features covering most of the codebase; refine later. `references/ubiquitous-language-template.md` has a fuller template with domain terms.
3. **Validate**: run `trellis_feature_info(project_id, feature_name="<Name>")` for each feature. If it reports zero functions, your `### Files` globs match nothing — fix the patterns until each feature maps to its files.
4. **Keep it current**: whenever you add, rename, or significantly change a feature, update its `project.md` section. That's the only file you maintain — Trellis auto-syncs the `feature-<name>` Architecture note from it on every `trellis_sync` and spec save (agent-authored notes without the auto-generated marker are never overwritten). After editing `project.md`, re-run the feature tool that depends on it — the file is re-read on each call, no re-sync needed.

## Core Principles

### 1. Discovery First

Before writing or modifying code, use Trellis to build mental models of the codebase. Sync the repository, list features, and inspect function details. Never write code in a vacuum.

**How**: Run `trellis_sync` + `trellis_list_modules` at the start of every session. (`trellis_sync` only builds the index the first time — after that a file watcher keeps the graph in sync with your edits automatically.)

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
- Code mentions: `@function_name` for referencing functions, methods, classes, or files in the code graph
- Bidirectional backlinks (auto-computed)
- YAML frontmatter tags

**How linking works:**
- `[[Note Title]]` resolves to an existing note by its ID, title, or slug. Trellis strips common prefixes like `Feature:`, `Decision:`, and `Note:`, so `[[Feature: Code Graph Bridge]]` and `[[Code Graph Bridge]]` can resolve to the same note.
- `@function_name` links to a code symbol. Trellis filters out prose placeholders (e.g., `@Function` in generic text) and only creates an edge when the symbol exists in the synced code graph. File names such as `@auth.py` also work.
- The combined graph returned by `trellis_knowledge_graph` contains note nodes, code nodes, and edges, so you can see which docs mention which code and vice versa.

**How**: Use `trellis_create_note` to capture knowledge.

### 4. Verify After Changes

Re-analyze after implementing changes to catch unintended side effects. The code graph reflects your edits automatically — the graph server watches the repo and re-indexes changed files — so no re-sync is needed for your own edits.

**How**: Run `trellis_analyze_impact` after changes. (Re-run `trellis_sync` only after large external changes like big git pulls or branch switches, or if results look stale.)

## Workflow

### Phase 1: Discovery (Always Do This First)

**Goal**: Understand the codebase structure

1. **Sync the repository**:
   ```
   trellis_sync(project_id="my-project", repo_path="/path/to/repo")
   ```
   *When: At the start of every session*
   *What it does: Indexes the codebase into the code graph*
   *Check the `project_md` field in the response: if it reports `missing`, creating `project.md` is your next mandatory step (see Project.md section above) before any feature-level work.*

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

4. **Trellis maintains the Architecture note for you**: the `feature-<name>` note (tag `feature`) is auto-generated from your `project.md` section on every `trellis_sync` and spec save. Do NOT hand-create or hand-update it — edit `project.md` instead. Use `trellis_create_note` only for knowledge that does NOT belong in `project.md`: cross-cutting decisions, divergence logs, integration plans.
   *When: Never mandatory. Optional notes are for rationale and plans beyond the spec.*
   *Why: One source of truth. Hand-duplicated architecture notes drift out of sync with project.md; generated ones can't.*

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

3. **Analyze the functions you changed**:
   ```
   trellis_analyze_impact(project_id="my-project", function_path="<changed_function>")
   ```
   *When: Verifying changes are safe*
   *Why not `trellis_analyze_diff`: whole-diff analysis is **disabled by default** — broad diffs run one impact analysis per changed function and time out. Run `trellis_analyze_impact` on each function you actually modified instead. `trellis_analyze_diff` is only available when the server is started with `TRELLIS_ENABLE_DIFF_ANALYSIS=1`, and even then it is hard-capped (`TRELLIS_MAX_DIFF_CHARS`, `TRELLIS_MAX_DIFF_FILES`, `TRELLIS_MAX_DIFF_FUNCTIONS`).*

## Key Tools Reference

### Code Graph Tools (14 tools)

| Tool | Parameters | Returns | When to Use |
|------|-----------|---------|-------------|
| `trellis_sync` | `project_id`, `repo_path`, `config_path`, `incremental` | Status, node count, file count | Once per project; again only after large pulls or stale results (the server file-watcher keeps the graph fresh) |
| `trellis_list_modules` | `project_id` | List of directories with symbol counts | Understand structure |
| `trellis_search_code` | `project_id`, `query`, `limit` | Matching functions/classes with paths | Find code by keyword |
| `trellis_ast_search` | `project_id`, `query`, `node_type`, `returns`, `params`, `limit` | Matching symbols filtered by structure/signature | Find code by type, return type, or params (e.g. all `class` nodes) |
| `trellis_get_function` | `project_id`, `function_path` | Function details (signature, file, line, source) | Inspect before modifying |
| `trellis_module_overview` | `project_id`, `module_path` | Module overview with symbols | Understand a code directory |
| `trellis_tour` | `project_id`, `path` | Dependency-ordered module reading list (foundational → entry) | Onboard to an unfamiliar codebase or subtree |
| `trellis_analyze_impact` | `project_id`, `function_path`, `depth_mode` | Risk level, affected counts, features | Before every change |
| `trellis_feature_info` | `project_id`, `feature_name` | Feature spec, functions, hot functions | Understand a project.md feature |
| `trellis_trace_path` | `project_id`, `from_feature`, `to_feature` | Dependency paths between features/modules | Trace dependencies |
| `trellis_trace_http_route` | `project_id`, `route_path`, `depth` | Handler + downstream calls for an HTTP route | Web projects with indexed routes (JS/TS Express-style) |
| `trellis_detect_hotspots` | `project_id`, `limit` | High-centrality functions | Find complex areas |
| `trellis_project_health` | `project_id`, `limit` | Chokepoints, circular imports, surprising couplings | Architecture audit; before large refactors |
| `trellis_get_graph` | `project_id`, `max_nodes` | Graph data (simplified view for large repos) | Visualization or analysis |

**Avoid `trellis_get_graph` unless you have a specific reason.** For projects with more than `max_nodes` functions (default 200) it returns a simplified view (modules + one representative function per file) instead of the full graph. For impact questions, prefer `trellis_analyze_impact`. For dependency questions, prefer `trellis_trace_path`. Use `trellis_get_graph` only when you need to visualize or run custom graph analysis outside Trellis.

### Doc Graph Tools (5 tools)

| Tool | Parameters | Returns | When to Use |
|------|-----------|---------|-------------|
| `trellis_create_note` | `project_id`, `note_id`, `title`, `content`, `tags` | Note ID, title, links, mentions | Create or **update** a knowledge note by `note_id` |
| `trellis_get_note` | `project_id`, `note_id` | Full note with backlinks | Read note content |
| `trellis_search_notes` | `project_id`, `query` | Matching notes with excerpts | Find notes by keyword |
| `trellis_delete_note` | `project_id`, `note_id` | Status message | Remove obsolete notes |
| `trellis_knowledge_graph` | `project_id`, `include_code` | Note graph (code nodes only with `include_code=True`) | Get overview |

**`trellis_knowledge_graph` defaults to notes only.** Passing `include_code=True` adds every function in the project as a node — expensive in tokens. Note contents in graph nodes are truncated; use `trellis_get_note` for full content.

### Analysis Tools (2 tools)

| Tool | Parameters | Returns | When to Use |
|------|-----------|---------|-------------|
| `trellis_get_boundary_map` | `project_id` | Module boundary map | Identify boundaries |
| `trellis_analyze_diff` | `project_id`, `diff`, `compare_branch` | Changed files, affected functions, impact report, risk level | **Disabled by default** (`TRELLIS_ENABLE_DIFF_ANALYSIS=1` to enable); prefer per-function `trellis_analyze_impact` |

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
- `max_nodes`: Max function nodes before a simplified view is returned (default: 200)
- Returns: Code graph nodes and edges (simplified view for large repos)

**trellis_trace_path**:
- `from_feature`: Source feature or module (can be project.md feature name)
- `to_feature`: Target feature or module
- Returns: Direct and indirect call/import edges

**trellis_detect_hotspots**:
- `project_id`: Project identifier
- `limit`: Number of hotspots to return (default: 20)
- Returns: Functions with the most incoming calls/imports

**trellis_analyze_diff** (disabled by default):
- Only registered when the server runs with `TRELLIS_ENABLE_DIFF_ANALYSIS=1`
- Hard-capped: `TRELLIS_MAX_DIFF_CHARS` (default 200k), `TRELLIS_MAX_DIFF_FILES` (default 50), `TRELLIS_MAX_DIFF_FUNCTIONS` (default 25)
- `diff`: Optional raw diff string. If not provided, automatically fetches from git working tree
- `compare_branch`: Branch to compare against (default: origin/main or main)
- Returns: Overall risk, changed files, affected functions with impact analysis
- Prefer `trellis_analyze_impact` per changed function for routine verification

**trellis_create_note**:
- `note_id`: Unique identifier (e.g., "feature-auth", "decision-jwt")
- `title`: Human-readable title
- `content`: Markdown content with `[[links]]` and `@mentions`
- `tags`: Comma-separated tags (optional)

## Decision Tree

```
Starting a new task?
  → Run: trellis_sync + trellis_list_modules
  → If sync reports project_md missing: CREATE project.md first (mandatory)

About to change code?
  → Phase 2: Strategy
    → Run: trellis_analyze_impact

Changed code?
  → Phase 4: Verification
    → Run: trellis_analyze_impact per changed function
    → Update the feature's project.md section (its note syncs itself)

Reviewing someone else's code?
  → Identify changed functions from the diff yourself
  → Run: trellis_analyze_impact on the risky ones
  → (trellis_analyze_diff exists but is disabled by default)

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
5. `trellis_analyze_impact` - Verify

### Pattern: Extracting a Feature/Module

1. `trellis_trace_path` - Find all dependencies on extracted code
2. `trellis_get_boundary_map` - Check current boundaries
3. `trellis_analyze_impact` on key functions - Assess impact
4. Create new module
5. Move code
6. Update imports
7. `trellis_analyze_impact` on moved functions - verify no orphaned references

### Pattern: Refactoring for Performance

1. `trellis_detect_hotspots` - Find high-centrality functions
2. `trellis_analyze_impact` - Assess impact of optimization
3. Implement changes
4. `trellis_analyze_impact` - verify contracts preserved
5. Update notes with performance decisions

### Pattern: Reviewing Code Changes

1. List the changed functions from the diff (e.g. `git diff --name-only`)
2. `trellis_analyze_impact` on each high-risk changed function
3. `trellis_get_function` - Inspect specific functions if needed
4. (`trellis_analyze_diff` automates this but is disabled by default — it times out on broad diffs)

### Pattern: Onboarding to New Codebase

1. `trellis_sync` - Index the repo
2. `trellis_tour` - Dependency-ordered reading list (foundational modules first)
3. `trellis_list_modules` - See high-level directory structure
4. `trellis_search_code` - Find entry points
5. `trellis_get_function` - Understand key functions
6. `trellis_feature_info` - Understand project.md features
7. `trellis_project_health` - Spot chokepoints and circular imports early
8. `trellis_create_note` - Document learnings

### Pattern: Documenting Architecture Decisions

1. `trellis_create_note` with note_id="decision-{topic}"
2. Include context, decision, consequences
3. Link related features with `[[Feature-Name]]`
4. Reference code with `@function_name`
5. Add divergence section if implementation differs

## Linking Notes to Code and Blast Radius

Trellis turns notes into first-class graph nodes. Use them to extend impact analysis beyond code edges.

### What can be linked

- **Notes to notes**: `[[Note Title]]` resolves by note ID, title, or slug. Aliases like `Feature:`, `Decision:`, `Note:`, and `Func:` are stripped automatically, so you can use readable titles and still link to a short note ID.
- **Notes to code**: `@function_name`, `@ClassName.method`, or `@file.py` creates an edge from the note to the matching symbol in the synced code graph. The symbol must exist in the code graph; otherwise it is recorded as unresolved.
- **Code to notes**: Auto-generated code notes (tagged `is_code_note`) link back to their function.
- **Feature notes**: Tag a note with `feature` to mark it as a feature node; tag with `decision` to mark it as a decision node.

### What cannot be linked directly

There is no syntax for linking to an arbitrary "graph section" or subgraph. You cannot write `[[@module_name]]` or `[[graph:subgraph]]`. Use a feature note or module note as the anchor instead, and mention the relevant functions inside it.

### Blast-radius use case

When `trellis_analyze_impact` reports that a function is high-risk, use `trellis_get_note` and `trellis_search_notes` to find notes that mention the function or the feature it belongs to. This surfaces:
- Design decisions that constrain the change
- Previous refactor notes that explain why the code is shaped this way
- Cross-feature dependencies documented in notes but not yet visible in the code graph
- Tests, runbooks, or ownership info tied to the symbol

Workflow:
1. Run `trellis_analyze_impact` on the function.
2. Run `trellis_search_notes` with the function or feature name.
3. Read any related notes; update them if the change affects their content.
4. Create a new note documenting the decision if the blast radius spans multiple features.

### Example note

```markdown
---
title: Icons Rendering Decision
tags: decision, icons, graphics
---

# Icons Rendering Decision

We moved icon rendering from canvas to SVG in @icon.js.

## Impact
- Feature: [[Icons]] depends on [[Graphics]] for @graphics.js helpers.
- @IconRegistry.loadIcons must be called before any icon is rendered.
- See [[Auth Refactor Decision]] for the same pattern in authentication.
```

## Reference Files

- **`references/impact-analysis.md`**: Detailed workflow for conducting impact analysis and interpreting results
- **`references/ubiquitous-language-template.md`**: Template for creating a `project.md` file that captures domain terminology and architectural decisions

Read the relevant reference file before Phase 2 (Strategy) of the workflow.

## Troubleshooting & Configuration Notes

### Tool returns `response_too_large`
- All tool responses are compact JSON with a hard size budget (`TRELLIS_MAX_RESPONSE_CHARS`, default 50000 chars) to protect your context window.
- Narrow the query: use a specific `module_path`, `feature_name`, or `function_path`, or lower `limit`/`max_nodes`.
- Only raise `TRELLIS_MAX_RESPONSE_CHARS` on the server if you truly need the full payload.

### `trellis_analyze_diff` is not in the tool list
- It is disabled by default because broad diffs time out (one impact analysis per changed function).
- To enable: start the server with `TRELLIS_ENABLE_DIFF_ANALYSIS=1`. Caps: `TRELLIS_MAX_DIFF_CHARS`, `TRELLIS_MAX_DIFF_FILES`, `TRELLIS_MAX_DIFF_FUNCTIONS`.
- Preferred alternative: run `trellis_analyze_impact` on each function you changed.

### Sync returns 0 nodes or 0 files
- Check that `repo_path` exists and is the repo root (not a subfolder).
- Verify the Trellis MCP server is running and listed in your agent's MCP config.
- Run `trellis_sync` without `incremental` to force a full rebuild.
- Ensure the repo contains parseable source files (not just binary assets).

### MCP server is not running
- If tool calls fail immediately with a connection error, the `trellis-core` MCP server process is not running or is not reachable on the configured port.
- Restart your agent/IDE; the server should start automatically if configured in `opencode.jsonc` or equivalent MCP settings.

### Edges seem stale or callers are missing
- The code graph can be wiped by background indexing. Re-run `trellis_sync` after any unexpected results.
- Re-run `trellis_analyze_impact` after re-syncing to refresh affected-function counts.

### Project ID naming
- `project_id` is arbitrary but must be consistent across calls for the same repo.
- Trellis stores the index under `TRELLIS_DATA_DIR/projects/{project_id}/`. Use a short, stable identifier such as `tui-image-editor` or `my-service`.

### Windows paths
- On Windows, pass paths as forward slashes or double-quoted backslashes: `K:\repos\trellis` or `K:/repos/trellis`.
- Always quote backslash paths in JSON/MCP config to avoid escape-sequence issues.

## Best Practices

1. **Sync once per project**: Run `trellis_sync` when you first work on a project; after that the server file-watcher keeps the graph fresh automatically
2. **project.md is mandatory**: If sync reports it missing, create it before feature-level work
3. **Analyze before changing**: Never modify code without running `trellis_analyze_impact`
4. **Document decisions**: Record decisions in the feature's `### Decisions` section in `project.md`; use `trellis_create_note` only for rationale and plans that don't belong in the spec
5. **Maintain project.md, not notes**: Update the feature's `project.md` section when you change it — Trellis auto-syncs its `feature-<name>` note; never hand-edit that note
6. **Verify after changes**: Re-analyze after implementation (the watcher has already re-indexed edited files)
7. **Track divergence**: Note when implementation differs from specification
8. **Use specific paths**: Reference functions by name from Trellis output
9. **Link everything**: Use `[[Note]]` and `@Function` to connect docs and code
10. **Read references**: Check `references/impact-analysis.md` before complex changes
11. **Re-sync only as a last resort**: Custom call edges wiped by re-indexing are restored automatically within a minute; if impact analysis still returns 0 callers unexpectedly after that, run `trellis_sync` for a full rebuild
