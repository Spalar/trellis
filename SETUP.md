# Setup Guide: code-graph-mcp Integration

## Overview

Trellis now uses **code-graph-mcp** as its core graph engine. The source code is included as a git submodule so we can:
- Audit and review all code
- Apply custom patches if needed
- Build from source for security
- Track exactly which version we're using

## Architecture

```
trellis/
├── third_party/code-graph-mcp/    # Git submodule (your fork)
│   ├── src/                       # Rust source code (auditable)
│   ├── Cargo.toml               # Rust manifest
│   └── ...
├── bin/                         # Compiled binary (gitignored, auto-built)
│   ├── code-graph-mcp           # Built from submodule
│   └── version.txt              # Tracks which version is built
├── scripts/
│   ├── build_bridge.py          # Build script
│   └── security_scan.py         # Security scanner
├── src/trellis/
│   └── bridge.py                # Python wrapper
└── ...
```

## Step 1: Fork code-graph-mcp

1. Go to: https://github.com/sdsrss/code-graph-mcp
2. Click **Fork** button (top right)
3. Name it `code-graph-mcp` (or whatever you prefer)
4. Note your fork URL: `https://github.com/YOUR_USERNAME/code-graph-mcp`

## Step 2: Add Submodule

```bash
# From trellis project root
git submodule add https://github.com/YOUR_USERNAME/code-graph-mcp.git third_party/code-graph-mcp

# Pin to a specific release version
cd third_party/code-graph-mcp
git checkout v0.17.3  # or latest stable
cd ../..

# Commit the submodule
git add .gitmodules third_party/code-graph-mcp
git commit -m "Add code-graph-mcp v0.17.3 as submodule"
```

## Step 3: Build the Binary

### Option A: Build from Source (Recommended)

**Prerequisites:**
- Rust toolchain: https://rustup.rs/

```bash
# Build release binary
python scripts/build_bridge.py

# Verify installation
ls bin/
# Should see: code-graph-mcp (or code-graph-mcp.exe on Windows)
```

### Option B: Download Pre-built Binary

```bash
# For Linux/macOS
curl -L https://github.com/YOUR_USERNAME/code-graph-mcp/releases/download/v0.17.3/code-graph-mcp-$(uname -s)-$(uname -m) \
  -o bin/code-graph-mcp
chmod +x bin/code-graph-mcp

# For Windows (PowerShell)
# Download from releases page manually
```

## Step 4: Security Scan

```bash
# Run security scan on submodule
python scripts/security_scan.py

# With expected hash verification
python scripts/security_scan.py --expected-hash a876769b364628b2a5d9e467d433d3f471f6060d
```

## Step 5: Test the Bridge

```python
# test_bridge.py
from src.trellis import CodeGraphBridge

# Auto-detects binary in bin/
bridge = CodeGraphBridge("/path/to/your/repo")

# Test basic functionality
health = bridge.health_check()
print(f"Nodes indexed: {health.get('total_nodes', 'N/A')}")

# Search for a function
results = bridge.search("authenticate user")
for r in results[:3]:
    print(f"  {r.get('name')}: {r.get('file_path')}")

# Impact analysis
impact = bridge.analyze_impact("authenticate_user")
print(f"Risk level: {impact.get('risk_level')}")
print(f"Affected functions: {len(impact.get('affected_functions', []))}")
```

## Updating code-graph-mcp

### When upstream releases a new version:

```bash
cd third_party/code-graph-mcp

# Fetch upstream changes
git fetch origin

# Review changes
git log --oneline v0.17.3..v0.17.4

# Checkout new version
git checkout v0.17.4

# Run security scan
python scripts/security_scan.py

# Build
python scripts/build_bridge.py

# Test your application
# ...

# Commit update
cd ../..
git add third_party/code-graph-mcp bin/
git commit -m "Update code-graph-mcp to v0.17.4 (audited)"
```

### When you need a custom patch:

```bash
cd third_party/code-graph-mcp

# Create branch for your changes
git checkout -b trellis-patches

# Make changes...
# Edit src/...
# Build and test: cargo build --release

# Commit in submodule
git add .
git commit -m "Add custom patch for X"

# Push to your fork
git push origin trellis-patches

# Pin trellis to your patched version
cd ../..
git add third_party/code-graph-mcp
git commit -m "Pin code-graph-mcp to patched version"
```

## Team Setup

New team members just need:

```bash
git clone --recurse-submodules https://github.com/YOUR_USERNAME/trellis.git
cd trellis
python scripts/build_bridge.py
```

Or if they already cloned without submodules:

```bash
git submodule update --init
python scripts/build_bridge.py
```

## Troubleshooting

### "code-graph-mcp binary not found"

```bash
# Build it
python scripts/build_bridge.py

# Or install via npm
npm install -g @sdsrs/code-graph
```

### "Rust toolchain not found"

Install Rust: https://rustup.rs/

Then restart your terminal.

### Submodule shows as empty directory

```bash
# You forgot --recurse-submodules when cloning
git submodule update --init --recursive
```

### Security scan fails

Review the output. Common issues:
- `cargo audit` not installed: `cargo install cargo-audit`
- Suspicious patterns: Review manually, may be false positives
- Hash mismatch: Ensure submodule is at expected commit

## Security Policy

1. **Never commit compiled binaries** - They are gitignored and auto-built
2. **Always scan before updating** - Run `security_scan.py`
3. **Pin to specific versions** - No floating references
4. **Review upstream changes** - Check diffs before updating
5. **Fork the repo** - Don't depend on upstream availability

## Next Steps

Now you can build features on top of code-graph-mcp:

1. **Spec Validation** - Validate code against project.md
2. **Web Dashboard** - Visual graph explorer
3. **PR Review Bot** - Automated PR analysis
4. **Python Workflows** - pytest/mypy integrations

See `src/trellis/bridge.py` for the full Python API.
