#!/usr/bin/env python3
"""Comprehensive security scanner for Trellis and code-graph-mcp.

This scanner checks for known malware, supply-chain attack, and exploit patterns
in both the code-graph-mcp Rust dependency and Trellis's own Python code.

Trellis is designed to work fully offline/air-gapped. Any network call is treated
as a critical finding and must be manually reviewed and justified.

Usage:
    python scripts/security_scan.py
    python scripts/security_scan.py --expected-hash <hash>
    python scripts/security_scan.py --skip-audit

Exit codes:
    0 = all critical/high checks passed
    1 = critical or high severity findings (release blocked)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple


class Severity(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class Finding:
    file: Path
    line: int
    category: str
    severity: Severity
    message: str
    code: str


class SecurityScanner:
    """Security scanner for Rust and Python source files."""

    # Patterns that indicate network activity. These are CRITICAL because
    # Trellis must work fully offline.
    NETWORK_PATTERNS: List[Tuple[str, Severity, str]] = [
        # Rust network crates and APIs
        (r"\breqwest::", Severity.CRITICAL, "HTTP client usage"),
        (r"\bhyper::", Severity.CRITICAL, "HTTP server/client usage"),
        (r"\bureq::", Severity.CRITICAL, "HTTP client usage"),
        (r"\bcurl::", Severity.CRITICAL, "HTTP client usage"),
        (r"\bquinn::", Severity.CRITICAL, "QUIC network usage"),
        (r"\bquinn-proto::", Severity.CRITICAL, "QUIC protocol usage"),
        (r"\btokio::net::", Severity.CRITICAL, "Async network usage"),
        (r"\bstd::net::", Severity.CRITICAL, "Standard network usage"),
        (r"\bsocket2::", Severity.CRITICAL, "Raw socket usage"),
        (r"\bstd::os::unix::net::", Severity.CRITICAL, "Unix socket usage"),
        (r"\basync_std::net::", Severity.CRITICAL, "Async network usage"),
        (r"\bawc::", Severity.CRITICAL, "Actix HTTP client"),
        (r"\bisahc::", Severity.CRITICAL, "HTTP client usage"),
        (r"\battohttpc::", Severity.CRITICAL, "HTTP client usage"),
        (r"\bminreq::", Severity.CRITICAL, "HTTP client usage"),
        (r"\bsurf::", Severity.CRITICAL, "HTTP client usage"),
        (r"\btungstenite::", Severity.CRITICAL, "WebSocket usage"),
        (r"\bwebsocket::", Severity.CRITICAL, "WebSocket usage"),
        (r"\bfastwebsockets::", Severity.CRITICAL, "WebSocket usage"),
        (r"\btrust_dns::", Severity.CRITICAL, "DNS client usage"),
        (r"\breqwasm::", Severity.CRITICAL, "HTTP client usage"),
        (r"\bgloo_net::", Severity.CRITICAL, "HTTP client usage"),
        # Python network modules and APIs
        (r"\bimport\s+requests\b", Severity.CRITICAL, "Python requests import"),
        (r"\bimport\s+urllib\b", Severity.CRITICAL, "Python urllib import"),
        (r"\bimport\s+http\.client\b", Severity.CRITICAL, "Python http.client import"),
        (r"\bimport\s+ftplib\b", Severity.CRITICAL, "Python FTP import"),
        (r"\bimport\s+smtplib\b", Severity.CRITICAL, "Python SMTP import"),
        (r"\bimport\s+aiohttp\b", Severity.CRITICAL, "Python aiohttp import"),
        (r"\bimport\s+httpx\b", Severity.CRITICAL, "Python httpx import"),
        (r"\bimport\s+websockets\b", Severity.CRITICAL, "Python websockets import"),
        (r"\bimport\s+socket\b", Severity.CRITICAL, "Python socket import"),
        (
            r"\brequests\.(get|post|put|delete|patch|head|options|request)\b",
            Severity.CRITICAL,
            "Python requests call",
        ),
        (
            r"\burllib\.request\.(urlopen|Request)\b",
            Severity.CRITICAL,
            "Python urllib request",
        ),
        (
            r"\bhttp\.client\.(HTTPConnection|HTTPSConnection)\b",
            Severity.CRITICAL,
            "Python http.client connection",
        ),
        (r"\bsocket\.socket\b", Severity.CRITICAL, "Python socket creation"),
        (r"\burlopen\s*\(", Severity.CRITICAL, "URL open call"),
        (
            r"\bhttpx\.(get|post|put|delete|patch|request|Client)\b",
            Severity.CRITICAL,
            "Python httpx call",
        ),
        # Generic URL/network indicators
        (r"https?://", Severity.HIGH, "Hardcoded HTTP(S) URL"),
        (r"\bws://", Severity.HIGH, "WebSocket URL"),
        (r"\bwss://", Severity.HIGH, "Secure WebSocket URL"),
        (r"\bftp://", Severity.HIGH, "FTP URL"),
    ]

    # Process execution patterns. HIGH because it can be used to run arbitrary code.
    PROCESS_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"\bstd::process::Command\b", Severity.HIGH, "Rust process execution"),
        (r"\bstd::process::Child\b", Severity.HIGH, "Rust child process"),
        (r"\bCommand::new\b", Severity.HIGH, "Rust Command creation"),
        (
            r"\bsubprocess\.(run|Popen|call|check_output|check_call)\b",
            Severity.HIGH,
            "Python subprocess call",
        ),
        (r"\bos\.system\s*\(", Severity.HIGH, "Python os.system call"),
        (r"\bos\.popen\s*\(", Severity.HIGH, "Python os.popen call"),
        (r"\bos\.spawn\w*\s*\(", Severity.HIGH, "Python os.spawn call"),
    ]

    # Code execution / dynamic evaluation. CRITICAL/HIGH.
    # Note: re.compile() is Python regex; build.compile() is Rust cc crate.
    CODE_EXEC_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"\beval\s*\(", Severity.CRITICAL, "eval() call"),
        (r"\bexec\s*\(", Severity.CRITICAL, "exec() call"),
        (r"\bcompile\s*\(", Severity.HIGH, "compile() call"),
        (r"\b__import__\s*\(", Severity.HIGH, "Dynamic Python import"),
        (r"\bimportlib\.import_module\s*\(", Severity.HIGH, "Dynamic Python import"),
        (r"\bProcessCommandParser\b", Severity.HIGH, "Potential command parser"),
    ]

    # File system operations that could be dangerous.
    FILE_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"\bstd::fs::remove_dir_all\b", Severity.HIGH, "Recursive directory deletion"),
        (r"\bstd::fs::remove_file\b", Severity.MEDIUM, "File deletion"),
        (r"\bstd::fs::remove_dir\b", Severity.MEDIUM, "Directory deletion"),
        (r"\bstd::fs::write\b", Severity.MEDIUM, "File write"),
        (r"\bstd::fs::rename\b", Severity.MEDIUM, "File rename"),
        (r"\bstd::fs::copy\b", Severity.LOW, "File copy"),
        (r"\bshutil\.rmtree\b", Severity.HIGH, "Python recursive delete"),
        (r"\bos\.remove\s*\(", Severity.MEDIUM, "Python file delete"),
        (r"\bos\.unlink\s*\(", Severity.MEDIUM, "Python file delete"),
        (r"\bos\.rmdir\s*\(", Severity.MEDIUM, "Python directory delete"),
        (r"\bos\.rename\s*\(", Severity.MEDIUM, "Python file rename"),
    ]

    # Unsafe code blocks. MEDIUM because they bypass Rust safety.
    UNSAFE_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"\bunsafe\s*\{", Severity.MEDIUM, "Unsafe code block"),
        (r"\bunsafe\s+fn\b", Severity.MEDIUM, "Unsafe function"),
        (r"\bunsafe\s+impl\b", Severity.MEDIUM, "Unsafe implementation"),
        (r"\bunsafe\s+trait\b", Severity.MEDIUM, "Unsafe trait"),
    ]

    # Environment access. MEDIUM because it can leak config/secrets.
    ENV_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"\bstd::env::", Severity.MEDIUM, "Rust environment access"),
        (r"\benv::\b", Severity.MEDIUM, "Rust environment access"),
        (r"\bos\.environ\b", Severity.MEDIUM, "Python environment access"),
        (r"\bos\.getenv\s*\(", Severity.MEDIUM, "Python getenv"),
        (r"\bos\.putenv\s*\(", Severity.HIGH, "Python setenv"),
        (r"\bdotenv\b", Severity.LOW, "dotenv usage"),
    ]

    # Build-time execution vectors. CRITICAL/HIGH for supply chain attacks.
    # Note: build.rs is expected for sqlite-vec C compilation.
    BUILD_TIME_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"\binclude_str!\s*\(", Severity.MEDIUM, "Embed file at compile time"),
        (r"\binclude_bytes!\s*\(", Severity.MEDIUM, "Embed binary at compile time"),
        (r"\bcompile_error!\s*\(", Severity.LOW, "Compile-time error"),
    ]

    # Allowed patterns with documented justification.
    # These are expected in code-graph-mcp for legitimate functionality.
    # Trellis builds with --no-default-features to disable embed-model.
    ALLOWED_PATTERNS: List[Tuple[str, str, str]] = [
        # (regex, category, justification)
        (
            r"ripgrep \(rg\) not found",
            "Hardcoded HTTP(S) URL",
            "Error message pointing to rg install docs; no actual request made",
        ),
        (
            r"https://invalid\.example\.com/nonexistent\.tar\.gz",
            "Hardcoded HTTP(S) URL",
            "Invalid test URL used in unit test",
        ),
        (
            r"https://github\.com/sdsrss/code-graph-mcp/releases/download/.*/models\.tar\.gz",
            "Hardcoded HTTP(S) URL",
            "Model download URL in embed-model feature (disabled in Trellis builds via --no-default-features)",
        ),
        (
            r"ureq::",
            "HTTP client usage",
            'ureq usage is inside #[cfg(feature = "embed-model")] block, disabled in Trellis builds',
        ),
        (
            r"use std::process::Command;",
            "Rust process execution",
            "Import of Command for ripgrep/Node.js shell-outs",
        ),
        (
            r"\brg\b",
            "Rust Command creation",
            "code-graph-mcp optionally shells out to ripgrep for fast text search",
        ),
        (
            r"\bnode\b",
            "Rust Command creation",
            "code-graph-mcp shells out to Node.js for JavaScript/TypeScript parsing",
        ),
        (
            r"\bCommand::new\(\"node\"\)",
            "Rust process execution",
            "Node.js invocation for JS/TS parsing",
        ),
        (
            r"\bCommand::new\(\"rg\"\)",
            "Rust process execution",
            "ripgrep invocation for fast text search",
        ),
        (
            r"sqlite_vec",
            "compile() call",
            "Rust cc crate compile() call in build.rs for sqlite-vec C extension",
        ),
        (
            r"extern \"C\"",
            "Rust FFI block",
            "FFI declarations for sqlite-vec C library integration",
        ),
        (
            r"re\.compile\s*\(",
            "compile() call",
            "Python regex compilation, not code execution",
        ),
        (
            r"\bbuild\.compile\s*\(",
            "compile() call",
            "Rust cc crate compile() for native C code, not code execution",
        ),
        (
            r"subprocess\.(Popen|run|call)",
            "Python subprocess call",
            "Trellis spawns the bundled code-graph-mcp binary as a subprocess",
        ),
        (
            r"webbrowser\.open\s*\(\s*['\"]http://localhost:\d+['\"]\s*\)",
            "Hardcoded HTTP(S) URL",
            "Trellis opens the user's default browser to localhost after starting",
        ),
        (
            r"https://rustup\.rs/",
            "Hardcoded HTTP(S) URL",
            "Error message pointing to Rust install docs; no actual request made",
        ),
        (
            r"shutil\.rmtree\(project_code_graph\)",
            "Python recursive delete",
            "Trellis removes old .code-graph directory during migration to data dir",
        ),
        (
            r"shutil\.rmtree\(release_dir\)",
            "Python recursive delete",
            "Release build cleans previous build directory",
        ),
        (
            r"shutil\.rmtree\(path,\s*onerror=onerror\)",
            "Python recursive delete",
            "Safe cleanup of previous build artifacts",
        ),
        (
            r"shutil\.rmtree\(data_dir,\s*ignore_errors=True\)",
            "Python recursive delete",
            "Clean up local .trellis data before rebuild",
        ),
    ]

    # FFI / dynamic loading. HIGH because it can load arbitrary native code.
    FFI_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"\bextern\s+\"[^\"]+\"\s+\{", Severity.HIGH, "Rust FFI block"),
        (r"\bextern\s+\"C\"\b", Severity.HIGH, "Rust C FFI"),
        (r"\bstd::mem::transmute\b", Severity.HIGH, "Memory transmute"),
        (r"\bstd::mem::forget\b", Severity.LOW, "Memory forget"),
        (r"\bdlopen\b", Severity.CRITICAL, "Dynamic library loading"),
        (r"\bLoadLibrary[AW]?\s*\(", Severity.CRITICAL, "Windows DLL loading"),
        (
            r"\bctypes\.(CDLL|WinDLL|PyDLL)\b",
            Severity.CRITICAL,
            "Python ctypes library loading",
        ),
        (r"\bctypes\.util\.find_library\b", Severity.HIGH, "Python library search"),
        (r"\bcc\.compile\b", Severity.HIGH, "Python C compiler"),
    ]

    # Encoding/obfuscation patterns that could hide payloads.
    OBFUSCATION_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"[A-Za-z0-9+/]{100,}={0,2}", Severity.LOW, "Potential base64 payload"),
        (r"\\x[0-9a-fA-F]{2}", Severity.LOW, "Hex escape sequence"),
        (r"\\u\{[0-9a-fA-F]+\}", Severity.LOW, "Unicode escape sequence"),
        (
            r"\bbase64\.(b64decode|decodestring|decodebytes)\b",
            Severity.MEDIUM,
            "Base64 decode",
        ),
        (
            r"\bpickle\.loads?\s*\(",
            Severity.HIGH,
            "Python pickle load (deserialization risk)",
        ),
        (r"\byaml\.load\s*\(", Severity.HIGH, "YAML unsafe load"),
    ]

    # Panic hooks and signal handlers that could mask behavior.
    HOOK_PATTERNS: List[Tuple[str, Severity, str]] = [
        (r"\bstd::panic::set_hook\b", Severity.MEDIUM, "Custom panic hook"),
        (r"\bpanic::set_hook\b", Severity.MEDIUM, "Custom panic hook"),
        (r"\bsignal_hook::", Severity.MEDIUM, "Signal hook usage"),
        (r"\bcrtlazy::", Severity.MEDIUM, "Lazy static with constructor"),
    ]

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.findings: List[Finding] = []

    def scan_file(self, file_path: Path) -> None:
        """Scan a single source file for all patterns."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return

        lines = content.splitlines()
        rel_path = file_path.relative_to(self.project_root)

        # Skip generated files and vendored dependencies
        if self._should_skip(file_path, rel_path):
            return

        all_patterns = (
            self.NETWORK_PATTERNS
            + self.PROCESS_PATTERNS
            + self.CODE_EXEC_PATTERNS
            + self.FILE_PATTERNS
            + self.UNSAFE_PATTERNS
            + self.ENV_PATTERNS
            + self.BUILD_TIME_PATTERNS
            + self.FFI_PATTERNS
            + self.OBFUSCATION_PATTERNS
            + self.HOOK_PATTERNS
        )

        for line_num, line in enumerate(lines, 1):
            for pattern, severity, category in all_patterns:
                # Skip URL patterns in comments/documentation if not a network API
                if (
                    category == "Hardcoded HTTP(S) URL"
                    and self._is_comment_or_string_only(line)
                ):
                    continue

                if re.search(pattern, line):
                    # Check allow-list
                    if self._is_allowed(line, category):
                        continue

                    # Test files are not shipped in release; downgrade severity
                    path_str = str(rel_path).replace("\\", "/")
                    if (
                        "/tests/" in path_str
                        or "/benches/" in path_str
                        or path_str.startswith("tests/")
                        or path_str.startswith("benches/")
                    ):
                        if severity in (Severity.CRITICAL, Severity.HIGH):
                            severity = Severity.LOW
                            category = f"{category} (test/bench code)"
                        elif severity == Severity.MEDIUM:
                            severity = Severity.INFO
                            category = f"{category} (test/bench code)"

                    self.findings.append(
                        Finding(
                            file=rel_path,
                            line=line_num,
                            category=category,
                            severity=severity,
                            message=f"{category} detected",
                            code=line.strip(),
                        )
                    )

    def _is_allowed(self, line: str, category: str) -> bool:
        """Check if a finding matches an allow-listed pattern."""
        for pattern, pat_category, _ in self.ALLOWED_PATTERNS:
            if pat_category == category and re.search(pattern, line):
                return True
        return False

    def _should_skip(self, file_path: Path, rel_path: Path) -> bool:
        """Skip generated, vendored, or non-source files."""
        skip_paths = [
            "target/",
            "vendor/sqlite-vec/sqlite-vec.c",
            "vendor/sqlite-vec/sqlite-vec.h",
            "node_modules/",
            ".venv/",
            "dist/",
            "build/",
            "__pycache__/",
            ".pytest_cache/",
            ".ruff_cache/",
        ]
        path_str = str(rel_path).replace("\\", "/")
        for skip in skip_paths:
            if skip in path_str:
                return True

        skip_extensions = {
            ".md",
            ".txt",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".lock",
            ".html",
            ".css",
            ".js",
        }
        if file_path.suffix.lower() in skip_extensions:
            return True

        return False

    def _is_comment_or_string_only(self, line: str) -> bool:
        """Check if a line containing a URL is only a comment or string."""
        stripped = line.strip()
        # Rust/Python comments
        if stripped.startswith("//") or stripped.startswith("#"):
            return True
        # String literals
        if (
            (stripped.startswith('"') and stripped.endswith('"'))
            or (stripped.startswith("'") and stripped.endswith("'"))
            or (stripped.startswith('r"') and stripped.endswith('"'))
            or (stripped.startswith("r'") and stripped.endswith("'"))
        ):
            return True
        return False

    def scan_directory(self, directory: Path) -> None:
        """Scan all source files in a directory."""
        for ext in ("*.rs", "*.py"):
            for file_path in directory.rglob(ext):
                self.scan_file(file_path)

    def print_report(self) -> bool:
        """Print findings grouped by severity. Return True if no critical/high."""
        if not self.findings:
            print("[PASS] No security patterns detected")
            return True

        by_severity: dict[Severity, List[Finding]] = {s: [] for s in Severity}
        for finding in self.findings:
            by_severity[finding.severity].append(finding)

        has_blocking = bool(
            by_severity[Severity.CRITICAL] or by_severity[Severity.HIGH]
        )

        for severity in [
            Severity.CRITICAL,
            Severity.HIGH,
            Severity.MEDIUM,
            Severity.LOW,
            Severity.INFO,
        ]:
            items = by_severity[severity]
            if not items:
                continue
            print(f"\n[{severity.value}] {len(items)} finding(s)")
            for finding in items:
                print(f"  {finding.file}:{finding.line}")
                print(f"    {finding.message}")
                print(f"    > {finding.code[:120]}")

        return not has_blocking


def run_cargo_audit(source_dir: Path, skip: bool = False) -> bool:
    """Run cargo audit on code-graph-mcp dependencies."""
    if skip:
        print("[INFO] Skipping cargo audit")
        return True

    print("[SCAN] Running cargo audit...")
    result = subprocess.run(
        ["cargo", "audit", "--version"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("[INFO] cargo-audit not installed. Installing...")
        install_result = subprocess.run(
            ["cargo", "install", "cargo-audit"],
            capture_output=True,
            text=True,
        )
        if install_result.returncode != 0:
            print("[FAIL] Could not install cargo-audit")
            return False

    result = subprocess.run(
        ["cargo", "audit"],
        cwd=source_dir,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    if "error" in output.lower() or result.returncode != 0:
        print("[FAIL] cargo audit found issues:")
        print(output)
        return False

    print("[PASS] cargo audit: no known vulnerabilities")
    return True


def verify_commit_hash(source_dir: Path, expected_hash: Optional[str]) -> bool:
    """Verify code-graph-mcp submodule commit hash."""
    if not expected_hash:
        return True

    print(f"[SCAN] Verifying commit hash: {expected_hash}...")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_dir,
        capture_output=True,
        text=True,
    )
    actual_hash = result.stdout.strip()

    if actual_hash != expected_hash:
        print("[FAIL] Hash mismatch!")
        print(f"   Expected: {expected_hash}")
        print(f"   Actual:   {actual_hash}")
        return False

    print("[PASS] Commit hash verified")
    return True


def get_code_graph_info(project_root: Path) -> Tuple[str, str]:
    """Return (commit_hash, version) for code-graph-mcp."""
    source_dir = project_root / "third_party" / "code-graph-mcp"

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    commit_hash = result.stdout.strip()

    cargo_toml = source_dir / "Cargo.toml"
    version = "unknown"
    if cargo_toml.exists():
        for line in cargo_toml.read_text().splitlines():
            if line.startswith("version"):
                version = line.split("=")[1].strip().strip('"')
                break

    return commit_hash, version


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Comprehensive security scan for Trellis release"
    )
    parser.add_argument(
        "--expected-hash",
        type=str,
        default=None,
        help="Expected git commit hash for code-graph-mcp submodule",
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip cargo audit (not recommended for releases)",
    )
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    source_dir = project_root / "third_party" / "code-graph-mcp"

    if not source_dir.exists():
        print(f"[FAIL] code-graph-mcp source not found: {source_dir}")
        sys.exit(1)

    commit_hash, version = get_code_graph_info(project_root)
    print("[SEC] Security scan for Trellis release")
    print(f"[SEC] code-graph-mcp v{version} @ {commit_hash}")
    print("=" * 60)

    results = []

    # 1. Static pattern scan of code-graph-mcp Rust source
    print("\n[SCAN] Scanning code-graph-mcp source patterns...")
    rust_scanner = SecurityScanner(source_dir)
    rust_scanner.scan_directory(source_dir)
    results.append(rust_scanner.print_report())

    # 2. Static pattern scan of Trellis Python source
    print("\n[SCAN] Scanning Trellis Python source patterns...")
    py_scanner = SecurityScanner(project_root)
    py_scanner.scan_directory(project_root / "src")
    py_scanner.scan_directory(project_root / "scripts")
    py_scanner.scan_file(project_root / "server.py")
    # Exclude vendored code-graph-mcp Python scripts and the scanner itself
    py_scanner.findings = [
        f
        for f in py_scanner.findings
        if "third_party" not in str(f.file).replace("\\", "/")
        and str(f.file) != "scripts\\security_scan.py"
    ]
    results.append(py_scanner.print_report())

    # 3. Cargo audit for known Rust vulnerabilities
    results.append(run_cargo_audit(source_dir, args.skip_audit))

    # 4. Commit hash verification
    results.append(verify_commit_hash(source_dir, args.expected_hash))

    print("\n" + "=" * 60)
    if all(results):
        print("[PASS] All security checks passed")
        sys.exit(0)
    else:
        print("[FAIL] Security checks failed. Release build aborted.")
        print("       Review CRITICAL and HIGH findings above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
