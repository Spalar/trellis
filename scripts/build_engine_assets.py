#!/usr/bin/env python3
"""Build linux/amd64 + linux/arm64 code-graph-mcp engine binaries via Docker.

Runs cargo inside the rust:1-bookworm container (works on a Windows host with
Docker Desktop), strips the binaries, writes checksums.sha256, smoke-verifies
them, and optionally publishes the three assets to the GitHub release for the
current Trellis version tag (VERSION in server.py, same source as
build_release.py):

    code-graph-mcp-linux-amd64
    code-graph-mcp-linux-arm64
    checksums.sha256

Downstream consumers (e.g. FlowState's docker/trellis.Dockerfile) download
them from:
    https://github.com/Spalar/trellis/releases/download/<tag>/code-graph-mcp-linux-<arch>

Usage:
    python scripts/build_engine_assets.py            # build into out/engine/
    python scripts/build_engine_assets.py --publish  # build, then create/update
                                                     # the GitHub release and
                                                     # attach the assets (gh CLI)
    python scripts/build_engine_assets.py --publish --force  # overwrite existing
                                                     # release assets

The build never modifies third_party/code-graph-mcp: the cargo target dir is
redirected to a container-local path. A named Docker volume caches the cargo
registry across runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBMODULE = REPO_ROOT / "third_party" / "code-graph-mcp"
OUT_DIR = REPO_ROOT / "out" / "engine"
SERVER_PY = REPO_ROOT / "server.py"

RUST_IMAGE = "rust:1-bookworm"
SMOKE_IMAGE = "debian:bookworm-slim"
CARGO_CACHE_VOLUME = "trellis-engine-cargo"

ASSET_AMD64 = "code-graph-mcp-linux-amd64"
ASSET_ARM64 = "code-graph-mcp-linux-arm64"
ASSETS = (ASSET_AMD64, ASSET_ARM64)
CHECKSUMS_FILE = "checksums.sha256"

REPO_SLUG = "Spalar/trellis"

# Deliberate fast-build profile override: the upstream profile's fat LTO +
# single codegen unit multiplies build time for an I/O-bound sidecar (same
# override FlowState uses for source builds).
FAST_BUILD_EXPORTS = (
    "export CARGO_PROFILE_RELEASE_LTO=false && "
    "export CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 && "
)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, raising with its output on failure."""
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kwargs)


def get_version() -> str:
    """Read VERSION from server.py (same source build_release.py uses)."""
    match = re.search(
        r'^VERSION = "([^"]+)"', SERVER_PY.read_text(encoding="utf-8"), re.MULTILINE
    )
    if not match:
        sys.exit("[FAIL] VERSION not found in server.py")
    return match.group(1)


def pinned_engine_sha() -> str:
    """Resolve the engine commit pinned by the superproject's gitlink.

    Fails loudly if the submodule is not checked out.
    """
    if not (SUBMODULE / "Cargo.toml").exists():
        sys.exit(
            "[FAIL] third_party/code-graph-mcp is not checked out. "
            "Run: git submodule update --init"
        )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD:third_party/code-graph-mcp"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        sys.exit(
            "[FAIL] Could not resolve pinned engine SHA via "
            "'git rev-parse HEAD:third_party/code-graph-mcp':\n" + result.stderr
        )
    return result.stdout.strip()


def docker_run(script: str) -> None:
    """Run a build script inside the rust container (see module docstring)."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{SUBMODULE.resolve().as_posix()}:/src",
        "-v",
        f"{OUT_DIR.resolve().as_posix()}:/out",
        "-v",
        f"{CARGO_CACHE_VOLUME}:/usr/local/cargo",
        "-w",
        "/src",
        RUST_IMAGE,
        "sh",
        "-c",
        script,
    ]
    print(f"[BUILD] docker run ... sh -c '<{script[:80]}...>'"[:140])
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        sys.exit(
            f"[FAIL] docker build failed (exit {result.returncode}).\n"
            f"--- stdout tail ---\n{result.stdout[-3000:]}\n"
            f"--- stderr tail ---\n{result.stderr[-3000:]}"
        )


def build_amd64() -> None:
    """Build the linux/amd64 engine binary into /out (container paths)."""
    script = (
        FAST_BUILD_EXPORTS
        + "cargo build --release --locked --no-default-features "
        + "--target-dir /build && "
        + f"strip -o /out/{ASSET_AMD64} /build/release/code-graph-mcp"
    )
    docker_run(script)
    print(f"[PASS] built {ASSET_AMD64}")


def build_arm64() -> None:
    """Cross-build the linux/arm64 engine binary into /out (container paths)."""
    script = (
        "apt-get update && apt-get install -y gcc-aarch64-linux-gnu "
        "binutils-aarch64-linux-gnu && "
        "rustup target add aarch64-unknown-linux-gnu && "
        + FAST_BUILD_EXPORTS
        + "export CARGO_BUILD_TARGET=aarch64-unknown-linux-gnu && "
        + "export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER="
        + "aarch64-linux-gnu-gcc && "
        + "cargo build --release --locked --no-default-features "
        + "--target-dir /build && "
        + f"aarch64-linux-gnu-strip -o /out/{ASSET_ARM64} "
        + "/build/aarch64-unknown-linux-gnu/release/code-graph-mcp"
    )
    docker_run(script)
    print(f"[PASS] built {ASSET_ARM64}")


def sha256_of(path: Path) -> str:
    """Hex sha256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums() -> dict[str, str]:
    """Write checksums.sha256 (relative filenames) and return {asset: sha256}."""
    hashes = {name: sha256_of(OUT_DIR / name) for name in ASSETS}
    lines = [f"{hashes[name]}  {name}" for name in ASSETS]
    # newline="\n": LF-only — sha256sum -c rejects CRLF lines on Linux consumers.
    with open(OUT_DIR / CHECKSUMS_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return hashes


def smoke_verify() -> None:
    """Run each binary with --help in a minimal container.

    amd64 failure is fatal; arm64 failure is a warning only (binfmt may be
    absent on the host).
    """
    for name, fatal in ((ASSET_AMD64, True), (ASSET_ARM64, False)):
        binary = (OUT_DIR / name).resolve().as_posix()
        cmd = ["docker", "run", "--rm"]
        if name == ASSET_ARM64:
            cmd += ["--platform", "linux/arm64"]
        cmd += ["-v", f"{binary}:/engine:ro", SMOKE_IMAGE, "/engine", "--help"]
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode == 0:
            print(f"[PASS] smoke: {name} --help (exit 0)")
        elif fatal:
            sys.exit(
                f"[FAIL] amd64 smoke test failed (exit {result.returncode}):\n"
                + result.stderr[-2000:]
            )
        else:
            print(
                f"[WARN] arm64 smoke test failed (exit {result.returncode}) — "
                "likely missing binfmt/qemu on this host; the binary was still "
                "built and published. stderr: " + result.stderr[-500:]
            )


def publish_assets(tag: str, force: bool) -> None:
    """Create/update the GitHub release for tag and attach the three assets."""
    run(["gh", "--version"])  # fails loudly if the gh CLI is missing

    view = subprocess.run(
        ["gh", "release", "view", tag, "--json", "tagName,assets"],
        text=True,
        capture_output=True,
    )
    if view.returncode != 0:
        print(f"[PUBLISH] release {tag} does not exist — creating it")
        run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--title",
                f"Trellis {tag}",
                "--notes",
                "Engine binaries for downstream docker builds; see CHANGELOG.md.",
            ]
        )
        existing = set()
    else:
        release = json.loads(view.stdout)
        existing = {a["name"] for a in release.get("assets", [])}

    collision = existing & set(ASSETS) | (existing & {CHECKSUMS_FILE})
    if collision and not force:
        sys.exit(
            f"[FAIL] release {tag} already contains {sorted(collision)}. "
            "Re-run with --force to overwrite."
        )

    upload = ["gh", "release", "upload", tag]
    if force:
        upload.append("--clobber")
    upload += [str(OUT_DIR / name) for name in (*ASSETS, CHECKSUMS_FILE)]
    run(upload)
    print(f"[PUBLISH] attached {len(ASSETS) + 1} assets to {tag}")


def main() -> None:
    """Parse args, build both engines, checksum, smoke, optionally publish."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--publish",
        action="store_true",
        help="create/update the GitHub release for the current tag and attach assets",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --publish: overwrite assets that already exist on the release",
    )
    args = parser.parse_args()

    sha = pinned_engine_sha()
    version = get_version()
    tag = f"v{version}"
    print(f"[BUILD] engine SHA (pinned): {sha}")
    print(f"[BUILD] release tag: {tag}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_amd64()
    build_arm64()

    hashes = write_checksums()
    smoke_verify()

    for name in ASSETS:
        print(f"[SHA256] {name}: {hashes[name]}")
    if args.publish:
        publish_assets(tag, args.force)

    print("\nAssets (in out/engine/):")
    for name in (*ASSETS, CHECKSUMS_FILE):
        print(f"  - {name}")
    print("Download URLs:")
    for name in ASSETS:
        print(
            f"  https://github.com/{REPO_SLUG}/releases/download/{tag}/{name}"
        )


if __name__ == "__main__":
    main()
