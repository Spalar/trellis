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
- `function_path`: Function name, qualified name, or file:function format (e.g., `authenticateUser` or `src/services/auth.ts:authenticateUser`)
- `depth_mode`: "standard" (default) or "deep"

### Feature-Level Changes

Use `trellis_feature_info` plus `trellis_analyze_impact` on the feature's hot functions when:
- Adding or removing a feature
- Changing a feature's public API
- Restructuring how features interact
- Renaming or splitting features

Parameters:
- `project_id`: The project identifier
- `feature_name`: Name of the feature from `project.md` (e.g., `Icons`, `Authentication`)

## Workflow

1. **Identify the change boundary**
   - Is it contained within one function? -> function-level `trellis_analyze_impact`
   - Does it span multiple functions or change feature boundaries? -> `trellis_feature_info` then analyze key functions

2. **Run the analysis**
   - Read the returned risk level, affected function/file counts, and feature impacts
   - Note the development pointers and divergence warnings

3. **Interpret results**
   - Note all impacted functions/features
   - Pay attention to risk flags and recommended actions
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
Use trellis_feature_info to show UserService functions, then analyze impact of the most central ones.
```

**After implementation:**
```
I've refactored the database layer. Re-analyze impact to confirm
no unintended side effects remain.
```

## Common Patterns

### Adding a Parameter

1. Analyze impact on the function with `trellis_analyze_impact`
2. Check all callers via the returned `callers` list
3. Update callers in dependency order (leaves first, root last)
4. Re-verify with `trellis_analyze_impact`

### Extracting a Feature

1. Analyze feature impact on the source feature by inspecting its hot functions
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
