# Migration Guide: Old Engine → code-graph-mcp Bridge

## What Changed

### Old Way (Deleted)
```python
from engine import TrellisEngine
from store import GraphStore
from extractor import PythonTreeSitterExtractor

store = GraphStore()
engine = TrellisEngine(store=store, extractor=PythonTreeSitterExtractor())

# Slow: Parses all files, builds graph from scratch
result = engine.sync_project("my_project", "/path/to/repo", config, incremental=False)

# Our own (slower) implementations
impact = engine.analyze_impact("authenticate_user")
feature = engine.get_feature("authentication")
```

### New Way (Current)
```python
from src.trellis import CodeGraphBridge

# Fast: Uses compiled Rust binary
bridge = CodeGraphBridge("/path/to/repo")

# Their (faster) implementations
impact = bridge.analyze_impact("authenticate_user")
project = bridge.project_map()
module = bridge.module_overview("src/auth")
```

## File Mapping

| Old File | Status | New Equivalent |
|----------|--------|----------------|
| `extractor.py` | ❌ Deleted | `third_party/code-graph-mcp/src/parser/` |
| `engine.py` | ❌ Deleted | `src/trellis/bridge.py` |
| `impact_analyzer.py` | ❌ Deleted | `bridge.analyze_impact()` |
| `store.py` | ❌ Deleted | `third_party/code-graph-mcp/src/storage/` |
| `feature_intent.py` | ❌ Deleted | `bridge.search()` (semantic) |
| `router.py` | ❌ Deleted | `bridge.get_call_graph()` |
| `spec_manager.py` | ✅ Keep | Enhanced with bridge integration |
| `visualizer.html` | ✅ Keep | Adapts to bridge data format |
| `analytics.py` | ✅ Keep | Tracks bridge usage metrics |
| `auth.py` | ✅ Keep | Unchanged |
| `models.py` | ✅ Keep | Trimmed to our data structures |
| `server.py` | ✅ Keep | Rewrites tool handlers to use bridge |

## API Changes

### Impact Analysis
```python
# Old
report = engine.analyze_impact("authenticate_user")
for func in report.impacted_functions:
    print(func.function_path)
    print(func.confidence)

# New
impact = bridge.analyze_impact("authenticate_user")
for func in impact.get("affected_functions", []):
    print(func["name"])
    print(func["file_path"])
```

### Feature/Module Access
```python
# Old
feature = engine.get_feature("authentication")
for func in feature.functions:
    print(func.function_path)

# New
module = bridge.module_overview("src/auth")
for symbol in module.get("symbols", []):
    print(symbol["name"])
    print(symbol["kind"])  # function, class, etc.
```

### Search
```python
# Old
results = engine.search("authenticate", strategy="semantic")

# New
results = bridge.search("authenticate user")
# Returns list of dicts with name, file_path, score, etc.
```

## Data Format Differences

### Old (Our Format)
```python
{
    "function_path": "auth.authenticate_user",
    "file_path": "src/auth.py",
    "start_line": 42,
    "callers": ["api.login", "web.login"],
    "callees": ["db.get_user", "crypto.hash"]
}
```

### New (Their Format)
```python
{
    "name": "authenticate_user",
    "qualified_name": "auth::authenticate_user",
    "file_path": "src/auth.py",
    "line": 42,
    "kind": "function",
    "caller_count": 5,
    "callee_count": 3,
    "is_test": False,
    "docstring": "Authenticate a user..."
}
```

## Updating Your Code

### 1. Replace Imports
```python
# Remove these
from engine import TrellisEngine
from store import GraphStore
from extractor import PythonTreeSitterExtractor
from impact_analyzer import ImpactAnalyzer

# Use this
from src.trellis import CodeGraphBridge
```

### 2. Replace Initialization
```python
# Remove
store = GraphStore()
engine = TrellisEngine(store=store)

# Use
bridge = CodeGraphBridge("/path/to/repo")
```

### 3. Replace Method Calls

| Old Method | New Method | Notes |
|------------|------------|-------|
| `engine.sync_project()` | None needed | Auto-indexes on first use |
| `engine.get_feature()` | `bridge.module_overview()` | Different data format |
| `engine.analyze_impact()` | `bridge.analyze_impact()` | Same concept |
| `engine.trace_path()` | `bridge.get_call_graph()` | More detailed |
| `engine.search()` | `bridge.search()` | Better (BM25 + vector) |
| `engine.get_function()` | `bridge.get_ast_node()` | More details |
| `engine.list_features()` | `bridge.project_map()` | Project-wide overview |

### 4. Handle Data Format Changes

```python
# Adapter function to convert their format to ours
def adapt_function(node: dict) -> dict:
    """Convert code-graph-mcp node to Trellis format."""
    return {
        "function_path": node["qualified_name"],
        "file_path": node["file_path"],
        "start_line": node.get("line", 0),
        "callers": [c["name"] for c in node.get("callers", [])],
        "callees": [c["name"] for c in node.get("callees", [])],
    }
```

## Keeping Old Features

Some features we built are still valuable and should be adapted:

### Spec Validation
```python
# Old: Used our own graph
from spec_manager import SpecManager
validator = SpecValidator(engine=engine)

# New: Uses their graph
from spec_manager import SpecManager
from src.trellis import CodeGraphBridge

bridge = CodeGraphBridge("/path/to/repo")
validator = SpecValidator(bridge=bridge)  # Pass bridge instead
```

### Web Visualizer
```python
# Old: Read from our SQLite store
functions = store.list_functions(project_id)

# New: Query their graph
functions = bridge.search("*", limit=1000)
# Then convert to visualizer format
```

### Analytics
```python
# Old: Tracked our sync metrics
analytics.track_sync(duration=12.0, functions=538)

# New: Track bridge usage
analytics.track_query(tool="analyze_impact", duration=0.3)
```

## Testing

```python
# Old tests
from tests.conftest import trellis_engine

def test_impact(trellis_engine):
    result = trellis_engine.analyze_impact("authenticate_user")
    assert len(result.impacted_functions) > 0

# New tests
from src.trellis import CodeGraphBridge

def test_impact():
    bridge = CodeGraphBridge("tests/fixtures/sample_repo")
    result = bridge.analyze_impact("authenticate_user")
    assert len(result.get("affected_functions", [])) > 0
```

## Common Issues

### "Binary not found"
Run: `python scripts/build_bridge.py`

### "Different data format"
Use adapter functions or update your code to use their format directly.

### "Missing feature X"
Check if code-graph-mcp has an equivalent:
- Their docs: https://github.com/sdsrss/code-graph-mcp#mcp-tools
- Our bridge exposes: `analyze_impact`, `search`, `get_call_graph`, `get_ast_node`, `find_references`, `project_map`, `module_overview`, `trace_http_route`, `find_dead_code`, `dependency_graph`

### "Need custom feature"
Build it on top of the bridge:
```python
class MyFeature:
    def __init__(self, bridge: CodeGraphBridge):
        self.bridge = bridge
    
    def my_custom_analysis(self):
        # Use bridge.search(), bridge.get_call_graph(), etc.
        # Add your unique logic
        pass
```

## Timeline

- **Day 1**: Replace engine initialization
- **Day 2**: Update impact analysis calls
- **Day 3**: Update search/feature calls
- **Day 4**: Update tests
- **Day 5**: Update web visualizer data source
- **Week 2**: Add new features on top of bridge

## Need Help?

1. Check `src/trellis/bridge.py` for full API
2. Run `python -m src.trellis` for demo
3. See `SETUP.md` for build instructions
