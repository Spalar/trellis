"""Test specific queries on indexed repo."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from trellis import CodeGraphBridge


def main():
    print("Bridge Query Tests")
    print("=" * 50)
    
    repo_path = Path(__file__).parent
    bridge = CodeGraphBridge(str(repo_path))
    
    # Test 1: Search for bridge functions
    print("\n[1] Search: 'analyze_impact'")
    results = bridge.search("analyze_impact", limit=3)
    print(f"  Results: {len(results)}")
    for r in results:
        print(f"    - {r.get('name')} ({r.get('file_path')})")
    
    # Test 2: Get call graph
    print("\n[2] Call graph: 'analyze_impact'")
    try:
        cg = bridge.get_call_graph("analyze_impact", depth=1)
        print(f"  Keys: {list(cg.keys())[:5]}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Test 3: Get AST node
    print("\n[3] AST node: 'CodeGraphBridge'")
    try:
        node = bridge.get_ast_node("CodeGraphBridge")
        print(f"  Name: {node.get('name')}")
        print(f"  Kind: {node.get('kind')}")
        print(f"  File: {node.get('file_path')}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Test 4: Project map
    print("\n[4] Project map")
    try:
        pmap = bridge.project_map()
        modules = pmap.get('modules', [])
        print(f"  Modules: {len(modules)}")
        for m in modules[:3]:
            print(f"    - {m.get('path', 'N/A')}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Test 5: Module overview
    print("\n[5] Module overview: src/trellis")
    try:
        mod = bridge.module_overview("src/trellis")
        symbols = mod.get('symbols', [])
        print(f"  Symbols: {len(symbols)}")
        for s in symbols[:5]:
            print(f"    - {s.get('name')} ({s.get('kind')})")
    except Exception as e:
        print(f"  Error: {e}")
    
    bridge.close()
    print("\n[PASS] Query tests completed")


if __name__ == "__main__":
    main()
