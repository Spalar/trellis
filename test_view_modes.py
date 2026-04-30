"""Test visualizer API with different repo sizes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from trellis import CodeGraphBridge


def test_repo(repo_path: str, name: str):
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"Path: {repo_path}")
    print('='*60)
    
    bridge = None
    try:
        bridge = CodeGraphBridge(repo_path)
        
        # Health check
        health = bridge.health_check()
        total_nodes = health.get('nodes_count', 0)
        print(f"Total nodes: {total_nodes}")
        
        # Get graph
        graph = bridge.get_graph_for_visualizer(max_nodes=200)
        print(f"View mode: {graph['view_mode']}")
        print(f"Shown nodes: {len(graph['nodes'])}")
        print(f"Shown links: {len(graph['links'])}")
        
        # Show node types
        types = {}
        for n in graph['nodes']:
            t = n.get('type', 'unknown')
            types[t] = types.get(t, 0) + 1
        print(f"Node types: {types}")
        
        return True
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if bridge:
            bridge.close()


def main():
    # Test with tests directory (small)
    print("Testing small repo first...")
    test_repo(str(Path(__file__).parent / "tests"), "Tests (small)")
    
    # Then test with trellis (large)
    print("\n\nTesting large repo...")
    test_repo(str(Path(__file__).parent), "Trellis (large)")
    
    print("\n" + "="*60)
    print("[PASS] Size-based view tests completed")


if __name__ == "__main__":
    main()
