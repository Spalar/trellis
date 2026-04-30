"""Test feature impact API."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from trellis import CodeGraphBridge


def test_feature_impact():
    print("Testing Feature Impact API")
    print("=" * 60)
    
    project_path = str(Path(__file__).parent)
    bridge = CodeGraphBridge(project_path)
    
    # Test with a function that should have feature context
    # Use a function from our code
    symbol = "analyze_impact"
    
    print(f"\n[1] Feature context for '{symbol}':")
    context = bridge.get_feature_context(symbol)
    if context:
        print(f"  Feature: {context['feature_name']}")
        print(f"  Decisions: {len(context['decisions'])}")
        print(f"  Constraints: {len(context['constraints'])}")
    else:
        print("  No feature context (expected - not in tests/project.md)")
    
    print(f"\n[2] Development pointers for '{symbol}':")
    pointers = bridge.get_development_pointers(symbol)
    print(f"  Count: {len(pointers)}")
    for p in pointers[:3]:
        print(f"  - {p}")
    
    print(f"\n[3] Divergence check for '{symbol}':")
    warnings = bridge.check_feature_divergence(symbol)
    print(f"  Warnings: {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    
    print(f"\n[4] Feature impact report for '{symbol}':")
    report = bridge.get_feature_impact(symbol)
    print(f"  Symbol: {report['symbol']}")
    print(f"  Feature impacts: {len(report['feature_impacts'])}")
    print(f"  Divergence warnings: {len(report['divergence_warnings'])}")
    print(f"  Pointers: {len(report['development_pointers'])}")
    
    bridge.close()
    
    print("\n" + "=" * 60)
    print("[PASS] Feature impact API tests completed")


if __name__ == "__main__":
    test_feature_impact()
