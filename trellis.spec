# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building a single-folder Trellis release.

Usage:
    pyinstaller trellis.spec

Output:
    Windows: trellis.exe (onefile) and trellis/ (folder), via build_release.py
    Linux:   trellis/ (onedir), via build_linux_release.py
"""

import sys
from pathlib import Path

block_cipher = None

# Project root
root = Path(SPECPATH).resolve()

from PyInstaller.utils.hooks import copy_metadata

# Data files to bundle
datas = [
    (str(root / "visualizer.html"), "."),
    (str(root / "vendor"), "vendor"),
    (str(root / "skills"), "skills"),
    (str(root / "docs"), "docs"),
    (str(root / "bin" / "version.txt"), "bin"),
]

# The engine binary. On Linux it goes in as a binary so it keeps its
# executable bit (scripts/build_linux_release.py builds there).
WINDOWS = sys.platform == "win32"
engine = root / "bin" / ("code-graph-mcp.exe" if WINDOWS else "code-graph-mcp")
binaries = []
if WINDOWS:
    datas.append((str(engine), "bin"))
else:
    binaries.append((str(engine), "bin"))

# Include package metadata so importlib.metadata works
datas += copy_metadata("fastmcp")
datas += copy_metadata("fastapi")
datas += copy_metadata("pydantic")
datas += copy_metadata("uvicorn")
datas += copy_metadata("starlette")
datas += copy_metadata("mcp")

# Hidden imports that PyInstaller may miss
hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "fastmcp",
    "fastmcp.server",
    "fastmcp.server.http",
    "fastmcp.server.stdio",
    "git",
    "tree_sitter",
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "src.trellis",
    "src.trellis.bridge",
    "src.trellis.core",
    "src.trellis.mcp_server",
    "src.trellis.api",
    "src.trellis.ui",
    "src.trellis.knowledge_graph",
    "src.trellis.impact_analyzer",
    "src.trellis.feature_impact",
    "src.trellis.python_call_indexer",
    "src.trellis.utils",
    "spec_manager",
]

a = Analysis(
    [str(root / "server.py")],
    pathex=[str(root), str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Never ship local .env files in the release bundle
a.datas = [(dest, source, kind) for dest, source, kind in a.datas if not Path(source).name == ".env"]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if WINDOWS:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="trellis",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )

    # Also create a one-folder build with all assets side-by-side
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="trellis",
    )
else:
    # Linux: one folder (trellis/trellis + trellis/_internal/). Starts without
    # unpacking to /tmp, and is what the .deb installs under /opt/trellis.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="trellis",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name="trellis",
    )
