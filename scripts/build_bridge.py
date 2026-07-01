#!/usr/bin/env python3
"""Build script for code-graph-mcp from submodule."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).parent.parent


def check_rust_toolchain() -> bool:
    """Check if Rust toolchain is available."""
    return shutil.which("cargo") is not None


def update_vulnerable_dependencies(source_dir: Path) -> None:
    """Apply known security updates to transitive dependencies.

    code-graph-mcp pins its dependencies in Cargo.lock. Some transitive
    dependencies have published security advisories after the lockfile was
    frozen. We update only the specific vulnerable crates to their latest
    compatible versions without changing the upstream submodule commit.

    This list must be kept in sync with scripts/security_scan.py findings.
    """
    vulnerable_crates = [
        "quinn-proto",
        "rustls-webpki",
        "tar",
        "paste",
        "anyhow",
        "memmap2",
        "rand",
    ]

    print("[PATCH] Applying security updates to transitive dependencies...")
    for crate in vulnerable_crates:
        result = subprocess.run(
            ["cargo", "update", "-p", crate],
            cwd=source_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[WARN] Could not update {crate}: {result.stderr.strip()}")
        else:
            print(f"[PATCH] Updated {crate}")


def build_code_graph(source_dir: Path, release: bool = True) -> Path:
    """Build code-graph-mcp from source.

    Builds without the 'embed-model' feature to keep the binary fully
    offline/air-gapped. The embed-model feature downloads model files from
    GitHub, which violates Trellis's offline-by-design constraint.

    Args:
        source_dir: Path to code-graph-mcp source directory
        release: Build in release mode (default: True)

    Returns:
        Path to compiled binary
    """
    if not check_rust_toolchain():
        print("[FAIL] Rust toolchain not found!")
        print("   Install from: https://rustup.rs/")
        print("   Or download pre-built binary from GitHub releases")
        sys.exit(1)

    # Apply security updates to transitive dependencies before building
    update_vulnerable_dependencies(source_dir)

    print(f"[BUILD] Building code-graph-mcp from {source_dir}...")
    print("[BUILD] Disabling embed-model feature to keep binary offline")

    # Build command - disable embed-model to avoid network downloads
    cmd = ["cargo", "build", "--no-default-features"]
    if release:
        cmd.append("--release")

    # Run build
    result = subprocess.run(cmd, cwd=source_dir, capture_output=True, text=True)

    if result.returncode != 0:
        print("[FAIL] Build failed!")
        print(result.stderr)
        sys.exit(1)

    # Find compiled binary
    target_dir = source_dir / "target" / ("release" if release else "debug")

    # Check multiple possible binary names
    binary_names = [
        "code-graph-mcp",
        "code-graph-mcp.exe",
    ]

    binary = None
    for name in binary_names:
        candidate = target_dir / name
        if candidate.exists():
            binary = candidate
            break

    if not binary:
        print("[FAIL] Binary not found after build!")
        print(f"   Checked: {target_dir}")
        sys.exit(1)

    print(f"[PASS] Build successful: {binary}")
    return binary


def install_binary(binary: Path, install_dir: Path) -> Path:
    """Install binary to project bin directory.

    Args:
        binary: Path to compiled binary
        install_dir: Destination directory

    Returns:
        Path to installed binary
    """
    install_dir.mkdir(parents=True, exist_ok=True)

    # Platform-specific binary name
    binary_name = "code-graph-mcp"
    if sys.platform == "win32":
        binary_name += ".exe"

    dest = install_dir / binary_name

    # Copy binary
    shutil.copy2(binary, dest)

    # Make executable (Unix only)
    if sys.platform != "win32":
        os.chmod(dest, 0o755)

    print(f"[PASS] Installed: {dest}")
    return dest


def get_version_from_source(source_dir: Path) -> str:
    """Read version from Cargo.toml."""
    cargo_toml = source_dir / "Cargo.toml"
    if not cargo_toml.exists():
        return "unknown"

    content = cargo_toml.read_text()
    for line in content.splitlines():
        if line.startswith("version"):
            return line.split("=")[1].strip().strip('"')

    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Build code-graph-mcp from submodule")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Build in debug mode (faster build, slower runtime)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to code-graph-mcp source (default: third_party/code-graph-mcp)",
    )
    args = parser.parse_args()

    # Determine source directory
    if args.source:
        source_dir = args.source
    else:
        source_dir = get_project_root() / "third_party" / "code-graph-mcp"

    if not source_dir.exists():
        print(f"[FAIL] Source directory not found: {source_dir}")
        print("   Run: git submodule update --init")
        sys.exit(1)

    # Get version
    version = get_version_from_source(source_dir)
    print(f"[PKG] code-graph-mcp version: {version}")

    # Build (security updates are applied inside build_code_graph)
    binary = build_code_graph(source_dir, release=not args.debug)

    # Install to project bin/
    install_dir = get_project_root() / "bin"
    installed = install_binary(binary, install_dir)

    # Write version file
    version_file = install_dir / "version.txt"
    version_file.write_text(f"{version}\n")

    print(f"\n[DONE] code-graph-mcp v{version} ready at: {installed}")
    print("\nTo use:")
    print("  1. Add 'bin/' to your PATH, or")
    print("  2. Use CodeGraphBridge which auto-detects the binary")


if __name__ == "__main__":
    main()
