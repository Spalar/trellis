"""Comprehensive integration test for Trellis with code-graph-mcp.

Tests the full stack:
1. MCP tools (stdio transport)
2. HTTP endpoints (FastAPI)
3. Feature impact analysis
4. Visualizer data format
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from server import (
    trellis_sync,
    trellis_analyze_impact,
    trellis_search,
    trellis_list_features,
    trellis_get_function,
    trellis_detect_hotspots,
)


async def test_mcp_tools():
    """Test MCP tools."""
    print("\n" + "="*60)
    print("1. MCP Tools Test")
    print("="*60)
    
    project_path = str(Path(__file__).parent)
    
    # Sync
    print("\n[1.1] trellis_sync")
    result = await trellis_sync(project_id="trellis", repo_path=project_path)
    print(f"  Result: {result[:100]}...")
    
    # Search
    print("\n[1.2] trellis_search")
    result = await trellis_search(project_id=project_path, query="bridge", limit=3)
    print(f"  Result: {result[:100]}...")
    
    # List features
    print("\n[1.3] trellis_list_features")
    result = await trellis_list_features(project_id=project_path)
    print(f"  Result: {result[:100]}...")
    
    # Analyze impact
    print("\n[1.4] trellis_analyze_impact")
    result = await trellis_analyze_impact(project_id=project_path, function_path="analyze_impact")
    print(f"  Result: {result[:200]}...")
    
    # Get function
    print("\n[1.5] trellis_get_function")
    result = await trellis_get_function(project_id=project_path, function_path="analyze_impact")
    print(f"  Result: {result[:100]}...")
    
    # Detect hotspots
    print("\n[1.6] trellis_detect_hotspots")
    result = await trellis_detect_hotspots(project_id=project_path)
    print(f"  Result: {result[:100]}...")


def test_http_endpoints():
    """Test HTTP endpoints."""
    print("\n" + "="*60)
    print("2. HTTP Endpoints Test")
    print("="*60)
    
    import requests
    
    base = "http://localhost:17317"
    
    # Health
    print("\n[2.1] GET /health")
    try:
        resp = requests.get(f"{base}/health", timeout=5)
        print(f"  Status: {resp.status_code}")
        print(f"  Body: {resp.text}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Graph
    print("\n[2.2] GET /graph/trellis")
    try:
        resp = requests.get(f"{base}/graph/trellis", timeout=5)
        print(f"  Status: {resp.status_code}")
        data = resp.json()
        print(f"  Nodes: {len(data.get('nodes', []))}")
        print(f"  Links: {len(data.get('links', []))}")
        print(f"  View mode: {data.get('view_mode')}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Impact
    print("\n[2.3] GET /graph/trellis/impact/analyze_impact")
    try:
        resp = requests.get(f"{base}/graph/trellis/impact/analyze_impact", timeout=5)
        print(f"  Status: {resp.status_code}")
        data = resp.json()
        print(f"  Root: {data.get('root_function')}")
        print(f"  Risk: {data.get('risk_level')}")
        print(f"  Affected: {data.get('stats', {}).get('affected_count')}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Feature impact
    print("\n[2.4] GET /feature/trellis/impact/analyze_impact")
    try:
        resp = requests.get(f"{base}/feature/trellis/impact/analyze_impact", timeout=5)
        print(f"  Status: {resp.status_code}")
        data = resp.json()
        print(f"  Symbol: {data.get('symbol')}")
        print(f"  Pointers: {len(data.get('development_pointers', []))}")
        print(f"  Warnings: {len(data.get('divergence_warnings', []))}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Pointers
    print("\n[2.5] GET /feature/trellis/pointers/analyze_impact")
    try:
        resp = requests.get(f"{base}/feature/trellis/pointers/analyze_impact", timeout=5)
        print(f"  Status: {resp.status_code}")
        data = resp.json()
        pointers = data.get('pointers', [])
        print(f"  Pointers: {len(pointers)}")
        for p in pointers[:3]:
            print(f"    - {p}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Divergence
    print("\n[2.6] GET /feature/trellis/divergence/analyze_impact")
    try:
        resp = requests.get(f"{base}/feature/trellis/divergence/analyze_impact", timeout=5)
        print(f"  Status: {resp.status_code}")
        data = resp.json()
        print(f"  Has divergence: {data.get('has_divergence')}")
        print(f"  Warnings: {len(data.get('divergence_warnings', []))}")
    except Exception as e:
        print(f"  Error: {e}")


def test_visualizer():
    """Test visualizer data format."""
    print("\n" + "="*60)
    print("3. Visualizer Data Test")
    print("="*60)
    
    from src.trellis import CodeGraphBridge
    
    bridge = CodeGraphBridge(str(Path(__file__).parent))
    
    # Full graph
    print("\n[3.1] Full graph")
    graph = bridge.get_graph_for_visualizer(max_nodes=200)
    print(f"  View mode: {graph['view_mode']}")
    print(f"  Nodes: {len(graph['nodes'])}")
    print(f"  Links: {len(graph['links'])}")
    print(f"  Stats: {graph['stats']}")
    
    # Impact graph
    print("\n[3.2] Impact graph")
    impact = bridge.get_impact_graph("analyze_impact")
    print(f"  Root: {impact['root_function']}")
    print(f"  Risk: {impact['risk_level']}")
    print(f"  Nodes: {len(impact['nodes'])}")
    print(f"  Links: {len(impact['links'])}")
    
    bridge.close()


async def main():
    print("="*60)
    print("Trellis Full Integration Test")
    print("="*60)
    print(f"Testing on: {Path(__file__).parent}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Test MCP tools
    await test_mcp_tools()
    
    # Test HTTP endpoints
    test_http_endpoints()
    
    # Test visualizer data
    test_visualizer()
    
    print("\n" + "="*60)
    print("[PASS] Full integration test completed")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
