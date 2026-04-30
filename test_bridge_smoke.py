#!/usr/bin/env python3
"""Quick test of CodeGraphBridge without needing the binary."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))


def test_import():
    """Test that imports work."""
    print("Testing imports...")
    
    try:
        from trellis import CodeGraphBridge
        print("[OK] Import successful")
        return True
    except ImportError as e:
        print(f"[FAIL] Import failed: {e}")
        return False


def test_binary_detection():
    """Test that binary detection works (before build)."""
    print("Testing binary detection...")
    
    try:
        from trellis import CodeGraphBridge
        bridge = CodeGraphBridge(".")
        print(f"[OK] Binary found: {bridge.binary_path}")
        return True
    except RuntimeError as e:
        print(f"[WARN] Binary not found (expected before build):")
        print(f"       {e}")
        return False


def main():
    print("Trellis Bridge Smoke Test")
    print("=" * 40)
    
    results = []
    
    results.append(test_import())
    results.append(test_binary_detection())
    
    print("\n" + "=" * 40)
    if any(results):
        print("[OK] Partial success (binary needs build)")
    else:
        print("[FAIL] Tests failed")
    
    print("\nNext steps:")
    print("  1. Add submodule: git submodule add <your-fork> third_party/code-graph-mcp")
    print("  2. Build binary: python scripts/build_bridge.py")
    print("  3. Test: python test_bridge_smoke.py")


if __name__ == "__main__":
    main()
