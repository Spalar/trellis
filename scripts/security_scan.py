#!/usr/bin/env python3
"""Security scanning for code-graph-mcp submodule."""

import argparse
import subprocess
import sys
from pathlib import Path


def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).parent.parent


def run_cargo_audit(source_dir: Path) -> bool:
    """Run cargo audit on Rust dependencies.
    
    Returns:
        True if no vulnerabilities found
    """
    print("🔍 Running cargo audit...")
    
    # Check if cargo-audit is installed
    result = subprocess.run(
        ["cargo", "audit", "--version"],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print("⚠️  cargo-audit not installed. Installing...")
        subprocess.run(
            ["cargo", "install", "cargo-audit"],
            check=True
        )
    
    # Run audit
    result = subprocess.run(
        ["cargo", "audit"],
        cwd=source_dir,
        capture_output=True,
        text=True
    )
    
    if "error" in result.stdout.lower() or result.returncode != 0:
        print("❌ Vulnerabilities found!")
        print(result.stdout)
        return False
    
    print("✅ No known vulnerabilities in dependencies")
    return True


def scan_for_suspicious_patterns(source_dir: Path) -> bool:
    """Scan for suspicious code patterns.
    
    Returns:
        True if no issues found
    """
    print("🔍 Scanning for suspicious patterns...")
    
    suspicious_patterns = {
        "Network operations": ["std::net::", "tokio::net::", "reqwest::"],
        "Process execution": ["std::process::Command", "std::process::Child"],
        "Unsafe blocks": ["unsafe {"],
        "File operations": ["std::fs::remove", "std::fs::write"],
        "Environment access": ["std::env::", "env::"],
    }
    
    src_dir = source_dir / "src"
    if not src_dir.exists():
        print("⚠️  Source directory not found, skipping pattern scan")
        return True
    
    issues = []
    
    for rust_file in src_dir.rglob("*.rs"):
        content = rust_file.read_text()
        rel_path = rust_file.relative_to(source_dir)
        
        for category, patterns in suspicious_patterns.items():
            for pattern in patterns:
                if pattern in content:
                    issues.append(f"{rel_path}: {category} ({pattern})")
    
    if issues:
        print("⚠️  Suspicious patterns found (manual review recommended):")
        for issue in issues[:20]:  # Limit output
            print(f"  - {issue}")
        
        if len(issues) > 20:
            print(f"  ... and {len(issues) - 20} more")
        
        return False
    
    print("✅ No suspicious patterns found")
    return True


def check_for_backdoors(source_dir: Path) -> bool:
    """Check for potential backdoors.
    
    Returns:
        True if clean
    """
    print("🔍 Checking for potential backdoors...")
    
    danger_patterns = [
        "eval(",
        "exec(",
        "include_bytes!",
        "include_str!",
    ]
    
    src_dir = source_dir / "src"
    issues = []
    
    for rust_file in src_dir.rglob("*.rs"):
        content = rust_file.read_text()
        rel_path = rust_file.relative_to(source_dir)
        
        for pattern in danger_patterns:
            if pattern in content:
                issues.append(f"{rel_path}: contains '{pattern}'")
    
    if issues:
        print("⚠️  Dangerous patterns found:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    
    print("✅ No dangerous patterns found")
    return True


def verify_checksum(source_dir: Path, expected_hash: str = None) -> bool:
    """Verify source checksum if provided.
    
    Args:
        source_dir: Source directory
        expected_hash: Expected git commit hash
        
    Returns:
        True if verified or no hash provided
    """
    if not expected_hash:
        return True
    
    print(f"🔍 Verifying commit hash: {expected_hash}...")
    
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_dir,
        capture_output=True,
        text=True
    )
    
    actual_hash = result.stdout.strip()
    
    if actual_hash != expected_hash:
        print(f"❌ Hash mismatch!")
        print(f"   Expected: {expected_hash}")
        print(f"   Actual:   {actual_hash}")
        return False
    
    print("✅ Commit hash verified")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Security scan for code-graph-mcp"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to code-graph-mcp source"
    )
    parser.add_argument(
        "--expected-hash",
        type=str,
        default=None,
        help="Expected git commit hash"
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip cargo audit (if cargo-audit not available)"
    )
    args = parser.parse_args()
    
    # Determine source directory
    if args.source:
        source_dir = args.source
    else:
        source_dir = get_project_root() / "third_party" / "code-graph-mcp"
    
    if not source_dir.exists():
        print(f"❌ Source directory not found: {source_dir}")
        sys.exit(1)
    
    print(f"🔒 Security Scan: {source_dir}")
    print("=" * 50)
    
    results = []
    
    # Run checks
    if not args.skip_audit:
        results.append(run_cargo_audit(source_dir))
    
    results.append(scan_for_suspicious_patterns(source_dir))
    results.append(check_for_backdoors(source_dir))
    
    if args.expected_hash:
        results.append(verify_checksum(source_dir, args.expected_hash))
    
    print("\n" + "=" * 50)
    if all(results):
        print("✅ All security checks passed!")
        sys.exit(0)
    else:
        print("⚠️  Some checks failed. Review output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
