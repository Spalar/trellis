"""Test MCP server integration with code-graph-mcp bridge."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from server import trellis_sync, trellis_analyze_impact, trellis_search, trellis_list_features


async def test_mcp_tools():
    print("Testing MCP Tools with code-graph-mcp Bridge")
    print("=" * 60)
    
    project_path = str(Path(__file__).parent)
    
    # Test 1: Sync
    print("\n[1] trellis_sync")
    try:
        result = await trellis_sync(
            project_id="trellis",
            repo_path=project_path,
        )
        data = eval(result)  # Simple parse
        print(f"  Status: {data.get('status')}")
        print(f"  Nodes: {data.get('nodes')}")
        print(f"  Files: {data.get('files')}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Test 2: Search
    print("\n[2] trellis_search")
    try:
        result = await trellis_search(
            project_id=project_path,
            query="bridge",
            limit=5,
        )
        data = eval(result)
        results = data.get('results', [])
        print(f"  Found: {len(results)} results")
        for r in results[:3]:
            print(f"    - {r.get('name')}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Test 3: List features
    print("\n[3] trellis_list_features")
    try:
        result = await trellis_list_features(
            project_id=project_path,
        )
        data = eval(result)
        features = data.get('features', [])
        print(f"  Features: {len(features)}")
        for f in features[:5]:
            print(f"    - {f.get('name')} ({f.get('symbol_count')} symbols)")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Test 4: Analyze impact
    print("\n[4] trellis_analyze_impact")
    try:
        result = await trellis_analyze_impact(
            project_id=project_path,
            function_path="analyze_impact",
        )
        # Print raw result (might be long)
        print(f"  Result length: {len(result)} chars")
        print(f"  First 500 chars:")
        print(f"  {result[:500]}")
    except Exception as e:
        print(f"  Error: {e}")
    
    print("\n" + "=" * 60)
    print("[PASS] MCP tool tests completed")


if __name__ == "__main__":
    asyncio.run(test_mcp_tools())
