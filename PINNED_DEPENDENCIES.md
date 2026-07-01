# Pinned Dependencies

This file documents the exact versions of external dependencies that Trellis ships with.

## code-graph-mcp

| Property | Value |
|----------|-------|
| Repository | https://github.com/Spalar/code-graph-mcp.git |
| Pinned Commit | `f0433b19609e1f2867f077b0fef98078290f21dc` |
| Version | `0.17.3` |
| Last Security Review | 2026-07-01 |

### Why This Version

code-graph-mcp is a critical dependency: it parses source code and builds the SQLite index that all Trellis analysis depends on. We pin an exact commit rather than a tag so that every Trellis release builds against a byte-for-byte known source tree.

### Updating code-graph-mcp

1. Update the submodule to the desired commit:
   ```bash
   cd third_party/code-graph-mcp
   git fetch origin
   git checkout <commit-hash>
   cd ../..
   git add third_party/code-graph-mcp
   ```

2. Run a full security review:
   ```bash
   python scripts/security_scan.py --expected-hash <commit-hash>
   ```

3. Rebuild the binary:
   ```bash
   python scripts/build_bridge.py
   ```

4. Update this file with the new commit hash and version.

5. Run Trellis tests end-to-end before releasing.

### Security Review Checklist

- [ ] `cargo audit` passes with no vulnerabilities
- [ ] Source diff reviewed for network, process, and file-system changes
- [ ] Submodule commit hash matches the hash recorded below
- [ ] Rebuilt binary hash matches the recorded binary hash

### Build Artifacts

| File | Source | Notes |
|------|--------|-------|
| `bin/code-graph-mcp.exe` | Built from `third_party/code-graph-mcp` at pinned commit | Windows x86_64 |
| `bin/version.txt` | Derived from `Cargo.toml` | Human-readable version |

### Security Patches Applied at Build Time

The upstream `Cargo.lock` at the pinned commit contains transitive dependencies with known security advisories. Trellis applies compatible security updates at build time without modifying the upstream submodule commit:

```bash
cargo update -p quinn-proto      # RUSTSEC-2026-0185
cargo update -p rustls-webpki    # RUSTSEC-2026-0049, RUSTSEC-2026-0098, RUSTSEC-2026-0099, RUSTSEC-2026-0104
cargo update -p tar              # RUSTSEC-2026-0067, RUSTSEC-2026-0068
cargo update -p paste            # RUSTSEC-2024-0436 (unmaintained)
cargo update -p anyhow           # RUSTSEC-2026-0190
cargo update -p memmap2          # RUSTSEC-2026-0186
cargo update -p rand             # RUSTSEC-2026-0097
```

These updates are applied automatically by `scripts/build_bridge.py` and verified by `scripts/security_scan.py`.

### Offline Build

Trellis builds `code-graph-mcp` with `--no-default-features` to disable the `embed-model` feature, which would otherwise download model files from GitHub. This keeps the release fully offline/air-gapped.
