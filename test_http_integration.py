"""Test HTTP server integration."""

import requests
import sys
import time
from pathlib import Path

BASE_URL = "http://localhost:17317"


def test_endpoint(method: str, path: str, expected_status: int = 200):
    """Test an HTTP endpoint."""
    url = f"{BASE_URL}{path}"
    try:
        if method == "GET":
            resp = requests.get(url, timeout=10)
        elif method == "POST":
            resp = requests.post(url, timeout=10)
        else:
            return False, f"Unknown method: {method}"
        
        success = resp.status_code == expected_status
        return success, f"Status: {resp.status_code}, Size: {len(resp.text)} bytes"
    except Exception as e:
        return False, str(e)


def main():
    print("Testing HTTP Server Integration")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print()
    
    # Wait for server
    print("Waiting for server...")
    for i in range(10):
        try:
            resp = requests.get(f"{BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                print("Server is running!")
                break
        except:
            pass
        time.sleep(1)
    else:
        print("Server not running. Start it with: python server.py")
        return
    
    tests = [
        ("GET", "/health", 200),
        ("GET", "/graph/trellis", 200),
        ("GET", "/graph/trellis/impact/analyze_impact", 200),
        ("GET", "/feature/trellis/impact/analyze_impact", 200),
        ("GET", "/feature/trellis/pointers/analyze_impact", 200),
        ("GET", "/feature/trellis/divergence/analyze_impact", 200),
    ]
    
    for method, path, expected in tests:
        print(f"\n{method} {path}")
        success, msg = test_endpoint(method, path, expected)
        status = "PASS" if success else "FAIL"
        print(f"  [{status}] {msg}")
    
    print("\n" + "=" * 60)
    print("[DONE] HTTP integration tests completed")


if __name__ == "__main__":
    main()
