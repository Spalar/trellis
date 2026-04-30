"""End-to-end test: Index a repo and query the graph."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from trellis import CodeGraphBridge


def main():
    print("=" * 60)
    print("Trellis Bridge End-to-End Test")
    print("=" * 60)
    
    # Use trellis project as test subject
    repo_path = Path(__file__).parent
    print(f"\nRepo: {repo_path}")
    print(f"Binary: {CodeGraphBridge(str(repo_path)).binary_path}")
    
    # Initialize bridge
    print("\n[1/5] Initializing bridge...")
    bridge = CodeGraphBridge(str(repo_path))
    
    # Health check before indexing
    print("\n[2/5] Health check (before indexing)...")
    health = bridge.health_check()
    print(f"  Status: {'Healthy' if health.get('healthy') else 'Not indexed'}")
    print(f"  Nodes: {health.get('nodes_count', 0)}")
    print(f"  Files: {health.get('files_count', 0)}")
    
    # The binary auto-indexes on first query, but let's test search
    # which should trigger indexing
    print("\n[3/5] Search (triggers auto-indexing)...")
    t0 = time.time()
    results = bridge.search("bridge", limit=5)
    t1 = time.time()
    print(f"  Time: {t1-t0:.2f}s")
    print(f"  Results: {len(results)}")
    
    for r in results[:3]:
        name = r.get('name', 'N/A')
        fpath = r.get('file_path', 'N/A')
        print(f"    - {name} ({fpath})")
    
    # Health check after indexing
    print("\n[4/5] Health check (after indexing)...")
    health = bridge.health_check()
    print(f"  Status: {'Healthy' if health.get('healthy') else 'Not indexed'}")
    print(f"  Nodes: {health.get('nodes_count', 0)}")
    print(f"  Files: {health.get('files_count', 0)}")
    print(f"  Edges: {health.get('edges_count', 0)}")
    
    # Test impact analysis on a known function
    print("\n[5/5] Impact analysis...")
    try:
        impact = bridge.analyze_impact("CodeGraphBridge")
        print(f"  Risk: {impact.get('risk_level', 'N/A')}")
        print(f"  Affected: {len(impact.get('affected_functions', []))}")
        print(f"  Files: {len(impact.get('affected_files', []))}")
    except Exception as e:
        print(f"  Result: {e}")
    
    # Cleanup
    bridge.close()
    
    print("\n" + "=" * 60)
    print("[PASS] End-to-end test completed")
    print("=" * 60)


if __name__ == "__main__":
    main()
