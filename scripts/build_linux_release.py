#!/usr/bin/env python3
"""Build the Linux releases of Trellis: amd64 and arm64, as .tar.gz and .deb.

Everything runs in Docker (Rancher Desktop or Docker Desktop on a Windows
host); arm64 runs under QEMU emulation, registered on first use.

Steps:
  1. Engine: scripts/build_engine_assets.py builds code-graph-mcp for both
     architectures into out/engine/ (skip with --skip-engine to reuse them).
     Each binary must report the version in bin/version.txt.
  2. Bundle: PyInstaller in python:3.11 on Debian 11, one container per
     architecture, with the dependency versions of this machine's
     environment (pip freeze as constraints) so Linux ships what was tested.
  3. Package: trellis-v<V>-linux-<arch>.tar.gz (unpack and run) and
     trellis_<V>_<arch>.deb (installs /opt/trellis, `trellis` on PATH).
  4. Smoke test: install each .deb on Debian 12, Ubuntu 22.04 and Ubuntu 24.04
     as a non-root user, index a small Python project, and call MCP tools
     over stdio. Unpack each tarball on Ubuntu 22.04 and run it.
  5. checksums.sha256 over the four files.

Output (out/linux/):
    trellis-v<V>-linux-amd64.tar.gz    trellis_<V>_amd64.deb
    trellis-v<V>-linux-arm64.tar.gz    trellis_<V>_arm64.deb
    checksums.sha256

Requires glibc 2.34 or newer on the target (the engine's floor): Ubuntu
22.04+, Debian 12+, RHEL/Rocky 9+.

Usage:
    python scripts/build_linux_release.py                 # both architectures
    python scripts/build_linux_release.py --arch amd64    # one architecture
    python scripts/build_linux_release.py --skip-engine   # reuse out/engine/
    python scripts/build_linux_release.py --skip-smoke    # build only

Docker context: uses $DOCKER_CONTEXT if set; otherwise the CLI's current one.
For Rancher Desktop with the Docker CLI pointed at Docker Desktop, run with
DOCKER_CONTEXT=default.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_engine_assets as engine_assets  # noqa: E402
import build_release  # noqa: E402

OUT_DIR = REPO_ROOT / "out" / "linux"
STAGE_DIR = OUT_DIR / ".stage"

BUILD_IMAGE = "python:3.11-slim-bullseye"  # glibc 2.31: below the engine's 2.34
PYINSTALLER = "pyinstaller==6.21.0"
SMOKE_IMAGES = ("debian:bookworm-slim", "ubuntu:22.04", "ubuntu:24.04")
TARBALL_SMOKE_IMAGE = "ubuntu:22.04"
ARCHES = ("amd64", "arm64")
GLIBC_FLOOR = "2.34"

# What the bundle needs from the repository (see trellis.spec).
BUNDLE_SOURCES = ("server.py", "spec_manager.py", "trellis.spec", "visualizer.html", "src", "vendor", "skills", "docs")

# Windows-only or build-only packages: never constraints for the Linux build.
SKIP_CONSTRAINTS = {"pywin32", "pywin32-ctypes", "pefile", "colorama", "pyinstaller", "pyinstaller-hooks-contrib"}


def docker(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], text=True, capture_output=capture, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        tail = f"\n--- stdout ---\n{(result.stdout or '')[-4000:]}\n--- stderr ---\n{(result.stderr or '')[-4000:]}"
        sys.exit(f"[FAIL] docker {' '.join(args[:6])} ... (exit {result.returncode}){tail if capture else ''}")
    return result


def mount(host: Path, container: str, readonly: bool = False) -> list[str]:
    return ["-v", f"{host.resolve().as_posix()}:{container}{':ro' if readonly else ''}"]


def ensure_docker(arches: list[str]) -> None:
    """Docker answers, and arm64 containers run (registers QEMU if needed)."""
    docker("version", "--format", "{{.Server.Version}}")
    if "arm64" not in arches:
        return
    probe = ("run", "--rm", "--platform", "linux/arm64", "debian:bookworm-slim", "uname", "-m")
    if docker(*probe, check=False).stdout.strip() != "aarch64":
        print("[SETUP] registering arm64 emulation (tonistiigi/binfmt)")
        docker("run", "--privileged", "--rm", "tonistiigi/binfmt", "--install", "arm64")
        if docker(*probe, check=False).stdout.strip() != "aarch64":
            sys.exit("[FAIL] arm64 containers do not run on this Docker host")


def dependencies() -> list[str]:
    """Runtime requirements: pyproject dependencies plus requirements.txt."""
    deps = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    lines = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    deps += [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    return sorted(set(deps))


def constraints() -> str:
    """This environment's package versions, as pip constraints."""
    frozen = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--exclude-editable"], text=True, capture_output=True, check=True
    ).stdout.splitlines()
    keep = []
    for line in frozen:
        name = line.split("==")[0].strip().lower()
        if "==" in line and name not in SKIP_CONSTRAINTS:
            keep.append(line.strip())
    return "\n".join(keep) + "\n"


def engine_version_expected() -> str:
    return (REPO_ROOT / "bin" / "version.txt").read_text(encoding="utf-8").strip()


def check_engine(arch: str) -> Path:
    """The engine binary for arch exists and reports bin/version.txt's version."""
    binary = engine_assets.OUT_DIR / f"code-graph-mcp-linux-{arch}"
    if not binary.exists():
        sys.exit(f"[FAIL] {binary} missing; run without --skip-engine")
    out = docker(
        "run", "--rm", "--platform", f"linux/{arch}", *mount(binary, "/engine", True),
        "debian:bookworm-slim", "/engine", "--version",
    ).stdout.strip()
    want = engine_version_expected()
    if out.split()[-1:] != [want]:
        sys.exit(f"[FAIL] {binary.name} reports '{out}', bin/version.txt says {want}; rebuild without --skip-engine")
    print(f"[VERIFY] {binary.name}: {out}")
    return binary


BUILD_SCRIPT = r"""
set -eu
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
apt-get update -qq && apt-get install -y -qq --no-install-recommends binutils >/dev/null
pip install -q --no-cache-dir -c /work/constraints.txt -r /work/requirements.txt "$PYINSTALLER"
mkdir -p /build/bin && cd /build
for item in $BUNDLE_SOURCES; do cp -r "/src/$item" .; done
cp /src/bin/version.txt bin/ && install -m 755 /engine bin/code-graph-mcp
find . -name __pycache__ -type d -prune -exec rm -rf {} +
pyinstaller trellis.spec --clean --noconfirm --log-level WARN --distpath /build/dist --workpath /build/work
test -x dist/trellis/trellis
test -x dist/trellis/_internal/bin/code-graph-mcp
chmod -R go-w dist/trellis
tar -C dist -cf /work/bundle.tar trellis
"""


def build_bundle(arch: str, engine: Path, stage: Path) -> Path:
    """PyInstaller onedir bundle for arch, as stage/bundle.tar."""
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "constraints.txt").write_text(constraints(), encoding="utf-8", newline="\n")
    (stage / "requirements.txt").write_text("\n".join(dependencies()) + "\n", encoding="utf-8", newline="\n")
    print(f"[BUILD] {arch}: PyInstaller in {BUILD_IMAGE} (arm64 runs emulated and takes longer)")
    docker(
        "run", "--rm", "--platform", f"linux/{arch}",
        "-e", f"PYINSTALLER={PYINSTALLER}", "-e", f"BUNDLE_SOURCES={' '.join(BUNDLE_SOURCES)}",
        *mount(REPO_ROOT, "/src", True), *mount(engine, "/engine", True), *mount(stage, "/work"),
        BUILD_IMAGE, "sh", "-c", BUILD_SCRIPT,
    )
    return stage / "bundle.tar"


def readme(version: str, arch: str) -> str:
    commit, cg_version = build_release.get_code_graph_info()
    return f"""Trellis v{version} (Linux {arch})
{'=' * 40}

Needs glibc {GLIBC_FLOOR}+ (Ubuntu 22.04+, Debian 12+, RHEL 9+). No Python needed.

Run from this folder (or install the .deb, which puts `trellis` on PATH):
  ./trellis                 UI + REST API + MCP over HTTP on 127.0.0.1:17317
                            (HTTP clients need the bearer token printed at
                            first start, kept in ~/.trellis/api-token)
  ./trellis mcp             MCP server over stdio, for coding agents
  ./trellis api | ui        REST API only / visualizer + API
  ./trellis sync <repo> [--project-id <id>] [--incremental]
                            Index a repository, then exit

MCP client config (stdio):
  command: /path/to/trellis   args: ["mcp"]

Data lives in ~/.trellis (TRELLIS_DATA_DIR to change it) and in each
repository's .code-graph/ folder.

Bundled code-graph-mcp: v{cg_version} ({commit[:12]})
"""


START_SCRIPT = """#!/bin/sh
# UI + REST API + MCP over HTTP on http://127.0.0.1:17317
cd "$(dirname "$0")"
( sleep 2; command -v xdg-open >/dev/null 2>&1 && xdg-open http://127.0.0.1:17317 >/dev/null 2>&1 ) &
exec ./trellis "$@"
"""


def control(version: str, arch: str, installed_kb: int) -> str:
    name = subprocess.run(["git", "config", "user.name"], text=True, capture_output=True, cwd=REPO_ROOT).stdout.strip()
    email = subprocess.run(["git", "config", "user.email"], text=True, capture_output=True, cwd=REPO_ROOT).stdout.strip()
    maintainer = f"{name or 'Trellis maintainers'} <{email or 'noreply@localhost'}>"
    return f"""Package: trellis
Version: {version}
Architecture: {arch}
Maintainer: {maintainer}
Installed-Size: {installed_kb}
Depends: libc6 (>= {GLIBC_FLOOR})
Section: devel
Priority: optional
Homepage: https://github.com/{engine_assets.REPO_SLUG}
Description: Code graph analysis and knowledge management for coding agents
 Trellis indexes a repository into a call and dependency graph and serves it
 to AI coding agents over MCP (stdio or HTTP), with a REST API and a
 visualizer. Bundles the code-graph-mcp engine; no Python needed.
"""


PACKAGE_SCRIPT = r"""
set -eu
cd /work && rm -rf pkg tgz && mkdir -p pkg/opt tgz
tar -C pkg/opt -xf bundle.tar
tar -C tgz -xf bundle.tar && mv tgz/trellis "tgz/$NAME"
cp README.txt "tgz/$NAME/" && cp /src/LICENSE "tgz/$NAME/"
install -m 755 start-trellis.sh "tgz/$NAME/"
tar -C tgz --owner=0 --group=0 -czf "/out/$NAME.tar.gz" "$NAME"

mkdir -p pkg/usr/bin pkg/usr/share/doc/trellis pkg/DEBIAN
ln -s /opt/trellis/trellis pkg/usr/bin/trellis
cp /src/LICENSE pkg/usr/share/doc/trellis/copyright
cp README.txt pkg/usr/share/doc/trellis/README.txt
cp control pkg/DEBIAN/control
sed -i "s/^Installed-Size: .*/Installed-Size: $(du -sk pkg/opt pkg/usr | awk '{s+=$1} END {print s}')/" pkg/DEBIAN/control
find pkg -type d -exec chmod 755 {} +
chmod 644 pkg/DEBIAN/control pkg/usr/share/doc/trellis/*
dpkg-deb --build --root-owner-group -Zxz pkg "/out/$DEB" >/dev/null
dpkg-deb --info "/out/$DEB" | sed -n '/Package:/,/Description:/p'
"""


def package(version: str, arch: str, stage: Path) -> tuple[Path, Path]:
    name = f"trellis-v{version}-linux-{arch}"
    deb = f"trellis_{version}_{arch}.deb"
    (stage / "README.txt").write_text(readme(version, arch), encoding="utf-8", newline="\n")
    (stage / "start-trellis.sh").write_text(START_SCRIPT, encoding="utf-8", newline="\n")
    (stage / "control").write_text(control(version, arch, 0), encoding="utf-8", newline="\n")
    out = docker(
        "run", "--rm", "-e", f"NAME={name}", "-e", f"DEB={deb}",
        *mount(REPO_ROOT, "/src", True), *mount(stage, "/work"), *mount(OUT_DIR, "/out"),
        "debian:bookworm-slim", "sh", "-c", PACKAGE_SCRIPT,
    ).stdout
    print(f"[PACKAGE] {arch}: {name}.tar.gz, {deb}\n" + "\n".join("    " + line for line in out.splitlines()))
    return OUT_DIR / f"{name}.tar.gz", OUT_DIR / deb


# Index a two-file project, then over MCP stdio: list tools and ask which
# functions call helper(). Runs as an unprivileged user.
SMOKE_USER_SCRIPT = r"""
set -eu
mkdir -p ~/demo/pkg && cd ~/demo
: > pkg/__init__.py
printf 'def helper():\n    return 1\n' > pkg/util.py
printf 'from pkg.util import helper\n\n\ndef main():\n    return helper()\n' > pkg/app.py
$TRELLIS sync ~/demo --project-id demo > ~/sync.log 2>&1 || { cat ~/sync.log; exit 1; }
init='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}'
ready='{"jsonrpc":"2.0","method":"notifications/initialized"}'
list='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
call='{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"trellis_analyze_impact","arguments":{"project_id":"demo","function_path":"pkg/util.py:helper"}}}'
( printf '%s\n%s\n%s\n' "$init" "$ready" "$list"; sleep 3; printf '%s\n' "$call"; sleep 15 ) \
  | timeout 120 $TRELLIS mcp > ~/mcp.out 2> ~/mcp.err || true
grep -q '"trellis_search_code"' ~/mcp.out || { echo "no tools/list answer"; tail -c 3000 ~/mcp.err; exit 1; }
grep -q 'pkg/app.py' ~/mcp.out || { echo "impact did not find pkg/app.py:main"; tail -c 3000 ~/mcp.out; tail -c 2000 ~/mcp.err; exit 1; }
echo "tools: $(grep -o '"name":"trellis_[a-z_]*"' ~/mcp.out | sort -u | wc -l), impact found pkg/app.py"
"""


def smoke_deb(arch: str, deb: Path) -> None:
    for image in SMOKE_IMAGES:
        script = (
            "set -eu; export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -qq && apt-get install -y -qq /pkg/trellis.deb >/dev/null; "
            "test \"$(readlink -f /usr/bin/trellis)\" = /opt/trellis/trellis; "
            "useradd -m tester; "
            "su tester -c 'TRELLIS=trellis sh /pkg/smoke.sh'; "
            "apt-get remove -y -qq trellis >/dev/null && test ! -e /opt/trellis/trellis"
        )
        out = docker(
            "run", "--rm", "--platform", f"linux/{arch}", *mount(deb, "/pkg/trellis.deb", True),
            *mount(STAGE_DIR / "smoke.sh", "/pkg/smoke.sh", True), image, "sh", "-c", script,
        ).stdout.strip().splitlines()
        print(f"[SMOKE] {arch} .deb on {image}: install, sync, MCP, remove OK ({out[-1] if out else ''})")


def smoke_tarball(arch: str, tarball: Path) -> None:
    script = (
        "set -eu; useradd -m tester; "
        "su tester -c 'mkdir ~/app && tar -C ~/app -xzf /pkg/t.tar.gz && "
        "TRELLIS=$(ls -d ~/app/*/)trellis sh /pkg/smoke.sh'"
    )
    out = docker(
        "run", "--rm", "--platform", f"linux/{arch}", *mount(tarball, "/pkg/t.tar.gz", True),
        *mount(STAGE_DIR / "smoke.sh", "/pkg/smoke.sh", True), TARBALL_SMOKE_IMAGE, "sh", "-c", script,
    ).stdout.strip().splitlines()
    print(f"[SMOKE] {arch} .tar.gz on {TARBALL_SMOKE_IMAGE}: OK ({out[-1] if out else ''})")


def write_checksums(files: list[Path]) -> Path:
    path = OUT_DIR / "checksums.sha256"
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in sorted(files)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arch", choices=ARCHES, action="append", help="build only this architecture (repeatable)")
    parser.add_argument("--skip-engine", action="store_true", help="reuse the engine binaries in out/engine/")
    parser.add_argument("--skip-smoke", action="store_true", help="build and package without the install tests")
    args = parser.parse_args()
    arches = args.arch or list(ARCHES)

    version = build_release.get_version()
    commit, _ = build_release.get_code_graph_info()
    print(f"[BUILD] Trellis v{version} for linux/{', linux/'.join(arches)}; engine @ {commit[:12]}")
    build_release.run_security_scan(commit)
    ensure_docker(arches)

    if not args.skip_engine:
        engine_assets.OUT_DIR.mkdir(parents=True, exist_ok=True)
        if "amd64" in arches:
            engine_assets.build_amd64()
        if "arm64" in arches:
            engine_assets.build_arm64()
        if set(arches) == set(ARCHES):
            engine_assets.write_checksums()  # out/engine/checksums.sha256 for the engine assets
    engines = {arch: check_engine(arch) for arch in arches}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True)
    (STAGE_DIR / "smoke.sh").write_text(SMOKE_USER_SCRIPT, encoding="utf-8", newline="\n")

    artifacts: list[Path] = []
    for arch in arches:
        stage = STAGE_DIR / arch
        build_bundle(arch, engines[arch], stage)
        tarball, deb = package(version, arch, stage)
        artifacts += [tarball, deb]
        if not args.skip_smoke:
            smoke_deb(arch, deb)
            smoke_tarball(arch, tarball)

    checksums = write_checksums(artifacts)
    shutil.rmtree(STAGE_DIR)
    print(f"\n[DONE] {OUT_DIR}")
    for path in [*artifacts, checksums]:
        print(f"  {path.name:40} {path.stat().st_size / (1024 * 1024):7.1f} MB")
    if args.skip_smoke:
        print("[WARN] install tests skipped (--skip-smoke)")


if __name__ == "__main__":
    main()
