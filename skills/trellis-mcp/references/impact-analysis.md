# Impact Analysis Workflow

Use this reference when deciding how and when to run impact analysis during Phase 2 (Strategy).

## Choosing the Right Tool

### Function-Level Changes

Use `trellis_analyze_impact` when:
- Modifying a single function's implementation
- Changing a function's signature
- Refactoring internal logic
- Moving a function to a different file

Parameters:
- `project_id`: The project identifier
- `function_path`: Full path to the function (e.g., `src/services/auth.ts:authenticateUser`)
- `change_summary`: Brief description of the planned change
- `include_suggestions`: true (default)

### Feature-Level Changes

Use `trellis_analyze_feature_impact` when:
- Adding or removing a feature
- Changing a feature's public API
- Restructuring how features interact
- Renaming or splitting features

Parameters:
- `project_id`: The project identifier
- `feature_name`: Name of the feature from `trellis_list_features`
- `change_summary`: Brief description of the planned change
- `include_suggestions`: true (default)

## Workflow

1. **Identify the change boundary**
   - Is it contained within one function? -> function-level
   - Does it span multiple functions or change feature boundaries? -> feature-level

2. **Run the analysis**
   - Always set `include_suggestions: true` to get actionable recommendations
   - Include a concise but specific `change_summary`

3. **Interpret results**
   - Note all impacted functions/features
   - Pay attention to suggestions for safer alternatives
   - Identify test coverage gaps in impacted areas

4. **Plan mitigation**
   - List all files that need test updates
   - Identify interfaces that need versioning or deprecation
   - Note features that need coordination

## Example Prompts

**Before refactoring:**
```
I want to change the return type of `parseConfig` from string to object.
Analyze impact and tell me what breaks.
```

**Before feature work:**
```
I'm adding a caching layer to the UserService feature.
Analyze feature impact and identify all features that call UserService.
```

**After implementation:**
```
I've refactored the database layer. Re-analyze impact to confirm
no unintended side effects remain.
```

## Common Patterns

### Adding a Parameter

1. Analyze impact on the function
2. Check all callers via `trellis_get_function` with `include_callers: true`
3. Update callers in dependency order (leaves first, root last)
4. Re-verify with `trellis_trace_path` from root to modified function

### Extracting a Feature

1. Analyze feature impact on the source feature
2. Use `trellis_trace_path` to find all dependencies on the extracted code
3. Create the new feature
4. Update dependencies
5. Re-sync and verify no orphaned references

### Deprecating a Function

1. Analyze impact to find all callers
2. Add deprecation marker
3. Create replacement function
4. Migrate callers one by one
5. Re-analyze to confirm zero callers before removal
