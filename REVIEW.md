# Trellis Code Review: Alignment with Core Mission

**Mission**: Reduce token usage for models, enable fast identification of functions/relations, prevent code changes without impact understanding, ensure strategic & fast execution.

**Review Date**: 2025-04-26
**Reviewer**: opencode
**Verdict**: Good foundation but several critical inefficiencies that waste tokens and slow execution.

---

## Critical Issues

### 1. No In-Memory Graph (Wastes Tokens on Every Request)
**File**: `store.py`
**Severity**: CRITICAL

**Problem**: Every operation reads from JSON files on disk. The graph is never loaded into memory.

**Evidence**:
```python
# store.py:72-79
list_features():
    for file in sorted((project_dir / "features").glob("*.json")):
        payload = self._read_json(file)  # DISK I/O for every feature
        features.append(FeatureRecord.model_validate(payload))

# engine.py:208-214
analyze_impact():
    impacted_features = sorted({
        self.store.load_function(project_id, fn).feature_name  # DISK I/O for every function
        for fn in impacted_functions
        if self.store.load_function(project_id, fn) is not None
    })
```

**Impact**: 
- Impact analysis on 100 functions = 100 disk reads
- Search = loads entire project from disk
- Visualizer = loads everything for every request
- This is O(n) disk I/O where n = number of items

**Fix**: Load the full graph into memory once, operate on it in RAM:
```python
class GraphStore:
    def __init__(self):
        self._cache: Dict[str, ProjectGraph] = {}  # project_id -> graph
    
    def load_project(self, project_id: str) -> ProjectGraph:
        if project_id not in self._cache:
            # Load once, cache in memory
            self._cache[project_id] = self._load_from_disk(project_id)
        return self._cache[project_id]
```

---

### 2. Duplicate File Reading (Wastes Tokens & Time)
**File**: `engine.py:136-159`, `extractor.py`
**Severity**: HIGH

**Problem**: `sync_project()` reads every source file TWICE:
1. Extractor reads files to parse functions
2. `_extract_file_intents()` reads the SAME files again

**Evidence**:
```python
# Step 1: Extractor reads files (line 42)
extracted = self.extractor.extract_repo(repo_path)

# Step 2: Intent extractor reads the SAME files again (line 47)
file_intents = self._extract_file_intents(extracted, repo_path)
# Inside _extract_file_intents:
for item in extracted:
    full_path = Path(repo_path) / item.file_path
    files_content[item.file_path] = full_path.read_text()  # DUPLICATE READ
```

**Impact**: 2x disk I/O for every sync. On a 100-file project, 200 file reads instead of 100.

**Fix**: Pass source content from extractor to intent extractor:
```python
# extractor.py should return file contents alongside parsed functions
# engine.py uses existing content instead of re-reading
```

---

### 3. Shallow Impact Analysis (Misses Critical Dependencies)
**File**: `engine.py:187-304`
**Severity**: HIGH

**Problem**: Impact analysis only follows "callers" (who calls this function). It misses:
- Data dependencies (who reads/writes the same data structures)
- Class inheritance relationships
- Shared constants/configuration
- Event-driven dependencies
- Side effects on global state

**Evidence**:
```python
# analyze_impact() line 208
impacted_functions = self._transitive_callers(project_id, root.function_path)
# ONLY follows callers - ignores everything else

# Risk is purely based on count (line 217-222)
if size > 20: risk = "high"
elif size > 8: risk = "medium"
# No semantic analysis of WHAT changed
```

**Impact**: Developer changes a global config function. Analysis says "low risk" because only 3 functions call it. But those 3 functions affect 50 features indirectly through data flow. Missed entirely.

**Fix**: Multi-dimensional impact analysis:
```python
class ImpactAnalyzer:
    def analyze(self, function_path, change_summary):
        impacts = {
            'call_graph': self._transitive_callers(function_path),  # Current
            'data_flow': self._shared_data_structures(function_path),  # NEW
            'class_hierarchy': self._class_relationships(function_path),  # NEW
            'semantic': self._semantic_impact(function_path, change_summary),  # LLM
        }
        return self._weighted_risk(impacts)
```

---

### 4. Feature Intent Never Used (Dead Code)
**File**: `feature_intent.py`, `engine.py:136-168`
**Severity**: MEDIUM

**Problem**: The feature intent extraction code exists but its results are stored and never used for anything meaningful.

**Evidence**:
```python
# engine.py:117-125
self.store.save_snapshot(project_id, {
    "file_intents": {k: v for k, v in file_intents.items()},  # Stored but NEVER read
})

# _map_files_to_features() uses it for feature naming
# But then the feature name is used for... nothing strategic
# LLM prompts don't use intents
# Impact analysis doesn't use intents
# Visualizer doesn't show intents
```

**Impact**: Wasted computation during sync, wasted storage, but no strategic value.

**Fix**: Use intents strategically:
- Group related features by intent similarity
- LLM prompts should include: "This feature is INTENDED to be: {intent}"
- Impact analysis should weight features with similar intents higher
- Visualizer should show intent categories as colors

---

### 5. Doc Generation Still Token-Heavy Despite Incremental Claims
**File**: `doc_manager.py`, `llm_agent.py`
**Severity**: MEDIUM

**Problem**: The incremental system works for file hashes but:
- LLM calls for overlap analysis happen EVERY TIME (not cached properly)
- `_generate_docs_llm()` regenerates index page every time
- Feature docs don't reuse previous LLM analysis of unchanged files
- `generate_feature_doc()` sends 2000+ chars of source code to LLM even when cached

**Evidence**:
```python
# llm_agent.py:372-377
async def generate_feature_doc(...):
    cache_key = self._cache_key("doc", project_id, feature_name, 
                               feature.description[:100], spec_context[:50],
                               source_code[:100] if source_code else "")
    # Cache key depends on description[:100] - if description changes slightly, 
    # ENTIRE doc is regenerated even if source code didn't change
```

**Impact**: On a 20-feature project with LLM enabled:
- First run: 20 LLM calls (docs) + 20 LLM calls (overlap) = 40 calls
- Second run with 1 changed file: Should be ~2 calls, but actually does 21 calls
- Token waste: ~80% of tokens are spent re-analyzing unchanged features

**Fix**: True two-tier caching:
```python
def _should_regenerate_feature(self, feature, changed_files):
    # Only regenerate if:
    # 1. Source files changed
    # 2. OR spec context changed significantly
    # 3. OR doc format version changed
    if any(f in changed_files for f in feature.files):
        return True
    if self._spec_hash_changed(feature):
        return True
    return False
```

---

### 6. Visualizer Wastes Tokens by Loading Entire Graph
**File**: `visualizer.html`, `server.py:61-73`
**Severity**: MEDIUM

**Problem**: Visualizer loads the COMPLETE graph on every page load.

**Evidence**:
```javascript
// visualizer.html:333
const [graphResp, docsResp, featureGraphResp, specResp] = await Promise.all([
    fetch(`${baseHost}/graph/${projectId}`),  // ALL nodes and links
    fetch(`${baseHost}/docs/${projectId}`),   // ALL docs
    fetch(`${baseHost}/feature-graph/${projectId}`),  // ALL features
    fetch(`${baseHost}/spec/${projectId}`),   // Full spec
]);
```

**Impact**: On a 500-function project:
- Graph JSON: ~50KB
- Repeated every time visualizer opens
- No pagination or lazy loading
- Browser renders ALL nodes at once (slow)

**Fix**: Lazy loading:
```python
# Server: Add pagination
@mcp.custom_route("/graph/{project_id}/nodes", methods=["GET"])
async def get_nodes(request):
    layer = request.query_params.get("layer", "feature")  # Only load features first
    limit = int(request.query_params.get("limit", "50"))
    offset = int(request.query_params.get("offset", "0"))
    return paginated_nodes(project_id, layer, limit, offset)
```

---

### 7. No Batch Operations (N+1 Problem)
**File**: `engine.py`, `store.py`
**Severity**: MEDIUM

**Problem**: Operations that need multiple items load them one at a time.

**Evidence**:
```python
# engine.py:400-404
if include_callers:
    callers = [
        loaded
        for loaded in (self.store.load_function(project_id, item) for item in fn.callers)
        if loaded is not None
    ]
# If a function has 50 callers, that's 50 disk reads
```

**Fix**: Batch loading:
```python
def load_functions_batch(self, project_id: str, paths: List[str]) -> List[FunctionRecord]:
    # Load all in one operation
    return [self._function_cache[path] for path in paths if path in self._function_cache]
```

---

### 8. LLM Agent Lacks Codebase Awareness
**File**: `llm_agent.py`
**Severity**: MEDIUM

**Problem**: LLM prompts don't leverage the graph structure we've built.

**Evidence**:
```python
# llm_agent.py:414-435
prompt = (
    f"Write comprehensive documentation for the '{feature_name}' feature.\n\n"
    f"## Feature Overview\n"
    f"- Name: {feature_name}\n"
    f"- Description: {feature.description[:300]}\n"
    f"- Purpose: {feature.purpose or 'To be determined'}\n"
    f"- Files: {', '.join(feature.files_involved)}\n\n"
    f"## Key Functions\n"
    f"{functions_summary}\n\n"
    # Missing: Call graph context
    # Missing: What features depend on this
    # Missing: What this feature depends on
    # Missing: Complexity metrics
)
```

**Impact**: LLM writes generic docs because it lacks the structural context we've already computed.

**Fix**: Enrich prompts with graph data:
```python
prompt += f"""
## Graph Context
- Functions called by this feature: {len(feature.callees)}
- Functions calling this feature: {len(feature.callers)}
- Direct dependencies: {', '.join(feature.dependencies)}
- Dependent features: {', '.join(feature.dependents)}
- Cyclomatic complexity: {feature.complexity}
- Is entry point: {feature.is_entry_point}
"""
```

---

## Summary Table

| Issue | Severity | Token Waste | Speed Impact | Fix Effort |
|-------|----------|-------------|--------------|------------|
| No in-memory graph | CRITICAL | High | Severe | Medium |
| Duplicate file reading | HIGH | Medium | High | Low |
| Shallow impact analysis | HIGH | N/A | Medium | High |
| Unused feature intent | MEDIUM | Low | Low | Low |
| Doc generation caching | MEDIUM | High | Medium | Medium |
| Visualizer full loads | MEDIUM | Medium | Medium | Medium |
| N+1 loading | MEDIUM | Low | High | Low |
| LLM lacks graph context | MEDIUM | High | Low | Low |

---

## Recommended Priority Order

1. **Fix in-memory graph** (biggest impact on both tokens and speed)
2. **Fix duplicate file reading** (easy win)
3. **Add batch loading** (easy win)
4. **Improve impact analysis** (strategic value)
5. **Fix doc caching** (token savings)
6. **Enrich LLM prompts** (doc quality)
7. **Lazy load visualizer** (UX improvement)
8. **Use feature intents** (strategic value)

---

## Positive Aspects

1. **Good incremental sync** - Only re-parses changed files
2. **Tiered LLM system** - Small/medium/large model selection
3. **Feature-level impact** - Analyzes at feature granularity, not just functions
4. **MK Docs integration** - Professional documentation output
5. **Graph visualization** - Interactive D3.js explorer
6. **Spec-driven development** - project.md integration
7. **Multi-language support** - Python/JS/TS extractors

The foundation is solid. The main issues are around caching, avoiding redundant work, and fully leveraging the graph data we already have.
