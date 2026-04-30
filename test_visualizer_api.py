from src.trellis import CodeGraphBridge

bridge = CodeGraphBridge('.')

# Test visualizer graph
print("Testing visualizer graph...")
graph = bridge.get_graph_for_visualizer()
print(f"View mode: {graph['view_mode']}")
print(f"Nodes: {len(graph['nodes'])}")
print(f"Links: {len(graph['links'])}")
print(f"Stats: {graph['stats']}")

# Show sample nodes
print("\nSample nodes:")
for n in graph['nodes'][:5]:
    print(f"  - {n['id']} ({n['type']})")

# Test impact graph
print("\nTesting impact graph...")
impact = bridge.get_impact_graph("CodeGraphBridge")
print(f"Root: {impact['root_function']}")
print(f"Risk: {impact['risk_level']}")
print(f"Affected: {impact['stats']['affected_count']}")

bridge.close()
print("\n[PASS] All tests passed")
