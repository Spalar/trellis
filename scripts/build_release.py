#!/usr/bin/env python3
"""Build a release archive for Trellis.

This script bundles Python, all dependencies, the code-graph-mcp binary,
and runtime assets into a single folder/zip that users can download and run.

The build happens entirely in `out/` (gitignored) so it never collides with
running MCP servers that execute `dist/trellis.exe`. Deploying into `dist/`
is a separate, explicit step.

Usage:
    python scripts/build_release.py          # build into out/
    python scripts/build_release.py --deploy # build, then copy artifacts to dist/

Output (in out/):
    out/trellis.exe                              # onefile executable
    out/trellis/                                 # onefolder collection
    out/start-trellis.bat, out/README.txt        # launcher + readme (inside out/trellis/)
    out/trellis-{version}-{platform}.zip         # release archive

Deploy (--deploy) copies trellis.exe, the trellis/ folder, and the zip into
dist/ — the location MCP configs run the exe from.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def get_version() -> str:
    """Read version from server.py or fall back to package version."""
    server_path = Path(__file__).parent.parent / "server.py"
    text = server_path.read_text()
    for line in text.splitlines():
        if "VERSION" in line and "=" in line:
            # Extract quoted version string
            start = line.find('"') + 1
            end = line.find('"', start)
            if start > 0 and end > start:
                return line[start:end]
    return "0.2.0"


def get_code_graph_info() -> tuple[str, str]:
    """Return pinned (commit_hash, version) for code-graph-mcp submodule."""
    root = Path(__file__).parent.parent
    source_dir = root / "third_party" / "code-graph-mcp"

    if not source_dir.exists():
        raise FileNotFoundError(
            "code-graph-mcp submodule not found. Run: git submodule update --init"
        )

    # Get commit hash
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    commit_hash = result.stdout.strip()

    # Get version from Cargo.toml
    cargo_toml = source_dir / "Cargo.toml"
    version = "unknown"
    if cargo_toml.exists():
        for line in cargo_toml.read_text().splitlines():
            if line.startswith("version"):
                version = line.split("=")[1].strip().strip('"')
                break

    return commit_hash, version


def verify_code_graph_binary() -> None:
    """Ensure the bundled code-graph-mcp binary exists and matches pinned source."""
    root = Path(__file__).parent.parent
    bin_dir = root / "bin"
    binary_name = "code-graph-mcp.exe" if sys.platform == "win32" else "code-graph-mcp"
    binary_path = bin_dir / binary_name
    version_file = bin_dir / "version.txt"
    source_dir = root / "third_party" / "code-graph-mcp"

    if not binary_path.exists():
        raise FileNotFoundError(
            f"code-graph-mcp binary not found at {binary_path}. "
            "Run: python scripts/build_bridge.py"
        )

    if not source_dir.exists():
        raise FileNotFoundError(
            "code-graph-mcp source not found. Run: git submodule update --init"
        )

    # Compare binary modification time with source modification time.
    # Only consider src/ and Cargo.toml/lock, not target/ build artifacts.
    tracked_paths = [
        source_dir / "src",
        source_dir / "Cargo.toml",
        source_dir / "Cargo.lock",
        source_dir / "build.rs",
    ]
    source_files = []
    for p in tracked_paths:
        if p.is_file():
            source_files.append(p)
        elif p.is_dir():
            source_files.extend(f for f in p.rglob("*") if f.is_file())

    if not source_files:
        raise RuntimeError("No tracked source files found for code-graph-mcp")

    source_mtime = max(f.stat().st_mtime for f in source_files)
    binary_mtime = binary_path.stat().st_mtime

    if binary_mtime < source_mtime:
        raise RuntimeError(
            "code-graph-mcp binary is older than its source. "
            "Rebuild with: python scripts/build_bridge.py"
        )

    # Verify version file matches source Cargo.toml
    pinned_commit, pinned_version = get_code_graph_info()
    if version_file.exists():
        bundled_version = version_file.read_text().strip()
        if bundled_version != pinned_version:
            raise RuntimeError(
                f"Version mismatch: bin/version.txt says {bundled_version}, "
                f"but submodule source is {pinned_version}. "
                "Rebuild with: python scripts/build_bridge.py"
            )

    print(f"[VERIFY] code-graph-mcp v{pinned_version} @ {pinned_commit[:12]}")
    print(f"[VERIFY] Binary up to date: {binary_path}")


def get_release_name(version: str) -> str:
    """Build release archive name based on platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        system = "macos"
    return f"trellis-v{version}-{system}-{machine}"


def run_security_scan(expected_hash: str) -> None:
    """Run security scan on code-graph-mcp before building release."""
    print("[SEC] Running security scan")

    scan_script = Path(__file__).parent / "security_scan.py"
    if not scan_script.exists():
        raise FileNotFoundError(f"Security scan script not found: {scan_script}")

    result = subprocess.run(
        [
            sys.executable,
            str(scan_script),
            "--expected-hash",
            expected_hash,
        ],
        capture_output=True,
        text=True,
    )

    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError("Security scan failed. Release build aborted.")


def run_pyinstaller(out_dir: Path) -> Path:
    """Run PyInstaller using trellis.spec, with all output under out_dir.

    The spec uses bare relative names (name="trellis"), so PyInstaller's
    --distpath applies as-is even when building from a spec file.
    """
    root = Path(__file__).parent.parent
    spec_path = root / "trellis.spec"

    if not spec_path.exists():
        raise FileNotFoundError(f"PyInstaller spec not found: {spec_path}")

    print(f"[BUILD] Running PyInstaller with {spec_path} (distpath={out_dir})")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(spec_path),
            "--clean",
            "--noconfirm",
            "--distpath",
            str(out_dir),
        ],
        cwd=str(root),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("PyInstaller build failed")

    return out_dir / "trellis"


def create_launcher(release_dir: Path) -> None:
    """Create a launcher script in the release directory."""
    is_windows = platform.system() == "Windows"

    if is_windows:
        launcher = release_dir / "start-trellis.bat"
        launcher.write_text(
            "@echo off\n"
            "echo Starting Trellis...\n"
            "echo URL: http://localhost:17317\n"
            "echo Press Ctrl+C to stop\n"
            "start http://localhost:17317\n"
            "trellis.exe\n"
        )
    else:
        launcher = release_dir / "start-trellis.sh"
        launcher.write_text(
            "#!/bin/bash\n"
            'echo "Starting Trellis..."\n'
            'echo "URL: http://localhost:17317"\n'
            'echo "Press Ctrl+C to stop"\n'
            "python -c \"import webbrowser; webbrowser.open('http://localhost:17317')\"\n"
            "./trellis\n"
        )
        launcher.chmod(0o755)

    print(f"[BUILD] Created launcher: {launcher}")


def create_readme(release_dir: Path, version: str) -> None:
    """Create a small README inside the release archive."""
    readme = release_dir / "README.txt"
    try:
        commit_hash, cg_version = get_code_graph_info()
    except Exception:
        commit_hash, cg_version = "unknown", "unknown"

    readme.write_text(
        f"Trellis v{version}\n"
        f"{'=' * 40}\n\n"
        "1. Extract this archive\n"
        "2. Run the launcher:\n"
        "   - Windows: double-click start-trellis.bat\n"
        "   - macOS/Linux: run ./start-trellis.sh\n"
        "3. Your browser will open http://localhost:17317\n\n"
        "No Python installation required.\n\n"
        f"Bundled code-graph-mcp: v{cg_version} ({commit_hash[:12]})\n"
    )
    print(f"[BUILD] Created {readme}")


def zip_release(source_dir: Path, zip_path: Path, archive_root: str) -> None:
    """Create a zip archive from source_dir with a top-level folder name."""
    print(f"[BUILD] Creating archive: {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in source_dir.rglob("*"):
            if file_path.is_file():
                arcname = (
                    f"{archive_root}/{file_path.relative_to(source_dir).as_posix()}"
                )
                zf.write(file_path, arcname)
    print(f"[BUILD] Archive size: {zip_path.stat().st_size / (1024 * 1024):.1f} MB")


def remove_directory_safely(path: Path) -> None:
    """Remove a directory, retrying on locked files on Windows."""
    if not path.exists():
        return

    # Data directories created by previous test runs can hold locks; nuke them first.
    for data_dir in path.rglob(".trellis"):
        if data_dir.is_dir():
            shutil.rmtree(data_dir, ignore_errors=True)

    def onerror(func, path, exc_info):
        import stat

        try:
            os.chmod(path, stat.S_IWUSR)
            func(path)
        except Exception:
            pass

    shutil.rmtree(path, onerror=onerror)
    if path.exists():
        raise RuntimeError(
            f"Could not remove {path} (files may be locked by another process)"
        )


def _with_lock_retry(description: str, operation) -> None:
    """Run a deploy operation, retrying on Windows file locks.

    A running MCP server locks dist\trellis.exe (and the onedir folder), so
    the copy can hit PermissionError (WinError 5). Retry a few times in case
    a client is restarting, then give up with an actionable message.
    Never kills processes — deploying over a running server is the caller's
    deliberate choice.
    """
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            operation()
            return
        except PermissionError as e:
            if attempt == attempts:
                raise PermissionError(
                    f"{description} is locked by a running process: {e}\n"
                    "trellis.exe / code-graph-mcp are still running from dist\\.\n"
                    "Close your MCP sessions or stop them first, e.g.:\n"
                    "  Get-Process trellis | Stop-Process -Force\n"
                    "Then re-run: python scripts/build_release.py --deploy"
                ) from e
            print(
                f"[DEPLOY] {description} is locked "
                f"(attempt {attempt}/{attempts}, retrying in 2s): {e}"
            )
            time.sleep(2)


def deploy_to_dist(out_dir: Path, dist_dir: Path, release_name: str) -> None:
    """Copy fresh build artifacts from out/ into dist/ (the runtime location)."""
    artifacts = [
        ("trellis.exe (onefile executable)", out_dir / "trellis.exe", dist_dir / "trellis.exe", "file"),
        ("trellis/ (onefolder collection)", out_dir / "trellis", dist_dir / "trellis", "dir"),
        (f"{release_name}.zip", out_dir / f"{release_name}.zip", dist_dir / f"{release_name}.zip", "file"),
    ]

    for description, source, target, kind in artifacts:
        if not source.exists():
            print(f"[DEPLOY] Skipping {description}: not present in {out_dir}")
            continue

        def _copy() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "dir":
                if target.exists():
                    remove_directory_safely(target)
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)

        _with_lock_retry(f"{description} -> {target}", _copy)
        print(f"[DEPLOY] {description} -> {target}")


def main() -> None:
    """Main build entry point."""
    parser = argparse.ArgumentParser(
        description="Build the Trellis release into out/ "
        "(gitignored, separate from the dist/ runtime location)."
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="After a successful build, copy the artifacts into dist/ "
        "(fails if trellis.exe is running from dist/)",
    )
    args = parser.parse_args()

    version = get_version()
    release_name = get_release_name(version)
    root = Path(__file__).parent.parent
    out_dir = root / "out"
    built_dir = out_dir / "trellis"
    onefile_exe = out_dir / "trellis.exe"
    zip_path = out_dir / f"{release_name}.zip"

    print(f"[BUILD] Building Trellis release {release_name}")

    # Verify code-graph-mcp binary is built from the pinned submodule
    pinned_commit, pinned_version = get_code_graph_info()
    verify_code_graph_binary()

    # Run security scan before release
    run_security_scan(pinned_commit)

    # Clean previous build output so we don't package stale files
    out_dir.mkdir(parents=True, exist_ok=True)
    if built_dir.exists():
        remove_directory_safely(built_dir)
    if onefile_exe.exists():
        onefile_exe.unlink()
    if zip_path.exists():
        zip_path.unlink()

    # Run PyInstaller into out/
    run_pyinstaller(out_dir)

    # Add launcher and readme
    create_launcher(built_dir)
    create_readme(built_dir, version)

    # Create zip archive with a versioned top-level folder
    zip_release(built_dir, zip_path, release_name)

    # Tidy up the unpackaged build folder
    remove_directory_safely(built_dir)

    print(f"[BUILD] Done. Artifacts built in: {out_dir}")
    print(f"[BUILD]   - {onefile_exe}")
    print(f"[BUILD]   - {zip_path}")

    if args.deploy:
        dist_dir = root / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        try:
            deploy_to_dist(out_dir, dist_dir, release_name)
        except PermissionError as e:
            print(f"[DEPLOY] FAILED:\n{e}", file=sys.stderr)
            sys.exit(1)
        print(f"[DEPLOY] Done. Artifacts deployed to: {dist_dir}")


if __name__ == "__main__":
    main()
