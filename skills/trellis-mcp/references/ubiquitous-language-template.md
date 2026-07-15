# Ubiquitous Language Template

Use this template to create a `project.md` file in the repository root. Update it during Phase 2 (Strategy) and Phase 3 (Implementation) whenever new domain terms or architectural decisions emerge.

## Template

```markdown
# Project Domain Dictionary

## Project Info
- Name: [project name]
- Repository: [repo URL]
- Trellis Project ID: [project_id]

## Domain Terms

| Term | Definition | Context | Synonyms to Avoid |
|------|------------|---------|-------------------|
| [Term] | [Precise meaning] | [Where used] | [Ambiguous alternatives] |

## Features

| Feature | Description | Key Files | Dependencies |
|---------|-------------|-----------|--------------|
| [Feature Name] | [What it does] | [Main files] | [Other features it uses] |

## Conventions

### Naming
- [How features are named]
- [How functions are named]
- [File organization rules]

### Boundaries
- [Module separation rules]
- [What belongs in which feature]
- [Interface design principles]

## Decisions

| Date | Decision | Rationale | Status |
|------|----------|-----------|--------|
| [Date] | [What was decided] | [Why] | [Active/Superseded] |
```

## How to Populate

1. **After Discovery (Phase 1)**
   - Fill Project Info with repository details
   - List features from `trellis_feature_info` or `trellis_list_modules` output
   - Map each feature to its key files using `trellis_module_overview` or `trellis_feature_info`

2. **During Strategy (Phase 2)**
   - Add domain terms as you encounter them in code
   - Document interface decisions from `trellis_trace_path` analysis
   - Record module boundary decisions

3. **During Implementation (Phase 3)**
   - Add new terms introduced by changes
   - Update feature dependencies if refactoring changes coupling
   - Log architectural decisions with dates

## Examples

### Domain Term Entry

```markdown
| Term | Definition | Context | Synonyms to Avoid |
|------|------------|---------|-------------------|
| Campaign | A scheduled outreach with a target audience | Marketing feature | "Project" (too vague), "Blast" (too informal) |
```

### Feature Entry

```markdown
| Feature | Description | Key Files | Dependencies |
|---------|-------------|-----------|--------------|
| AuthService | User authentication and session management | src/auth/*.ts | UserRepository, EmailService |
```

### Decision Entry

```markdown
| Date | Decision | Rationale | Status |
|------|----------|-----------|--------|
| 2024-01-15 | Use feature-based folder structure | Trellis analysis showed high cohesion within features | Active |
```

## Maintenance Rules

- Update this file before committing significant changes
- Review and prune obsolete terms quarterly
- Ensure new team members read this before making changes
- Reference specific file paths and line numbers from Trellis output when documenting decisions
