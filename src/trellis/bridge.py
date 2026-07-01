"""Python bridge to code-graph-mcp binary.

Provides a Pythonic API over the code-graph-mcp JSON-RPC interface.
Auto-detects and manages the binary lifecycle.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import cli_parsers
from .utils import get_code_graph_path


class CodeGraphBridge:
    """Bridge to code-graph-mcp binary via JSON-RPC over stdio.

    Usage:
        bridge = CodeGraphBridge("/path/to/repo")
        impact = bridge.analyze_impact("authenticate_user")
        print(impact["risk_level"])
    """

    def __init__(self, project_path: str, binary_path: str = None) -> None:
        """Initialize bridge.

        Args:
            project_path: Path to repository to analyze
            binary_path: Path to code-graph-mcp binary (auto-detected if None)
        """
        self.project_path = Path(project_path).resolve()
        self.binary_path = Path(binary_path) if binary_path else self._find_binary()
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._req_id = 0

        # Setup .code-graph in trellis data directory with symlink in project
        self.code_graph_path = get_code_graph_path(self.project_path)

        # Track when call edges were last rebuilt to avoid redundant work
        self._last_call_edge_check: float = 0.0

        # Register cleanup
        atexit.register(self.close)

    def _find_binary(self) -> Path:
        """Find code-graph-mcp binary.

        Search order:
        1. Project bin/ directory
        2. PATH environment variable
        3. Raise error with helpful message
        """
        # 1. Check project bin/
        project_bin = Path(__file__).parent.parent.parent / "bin" / "code-graph-mcp"
        if project_bin.exists():
            return project_bin

        # Windows variant
        if os.name == "nt":
            project_bin = project_bin.with_suffix(".exe")
            if project_bin.exists():
                return project_bin

        # 2. Check PATH
        path_binary = shutil.which("code-graph-mcp")
        if path_binary:
            return Path(path_binary)

        # Not found
        raise RuntimeError(
            "code-graph-mcp binary not found!\n"
            "\n"
            "To fix:\n"
            "  1. Build from source: python scripts/build_bridge.py\n"
            "  2. Or install: npm install -g @sdsrs/code-graph\n"
            "  3. Or specify path: CodeGraphBridge('/path', binary_path='/path/to/binary')"
        )

    def _ensure_running(self) -> None:
        """Ensure the subprocess is running."""
        if self._proc is not None and self._proc.poll() is None:
            return

        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return

            # Check if DB is locked by another process
            if self._is_db_locked():
                print("[Trellis] DB is locked, cleaning up zombie processes...")
                self._kill_zombie_processes()

            env = os.environ.copy()

            self._proc = subprocess.Popen(
                [str(self.binary_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(self.project_path),  # Run in project directory
                bufsize=1,  # Line buffered
            )

    def _kill_zombie_processes(self) -> None:
        """Kill any leftover code-graph-mcp processes for this project."""
        import subprocess

        try:
            # Try using taskkill on Windows
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/IM", "code-graph-mcp.exe"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                # Try using pkill on Unix
                subprocess.run(
                    ["pkill", "-9", "-f", "code-graph-mcp"],
                    capture_output=True,
                    timeout=10,
                )
        except Exception:
            pass

        # Also try psutil if available
        try:
            import psutil

            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                if proc.info["name"] and "code-graph-mcp" in proc.info["name"]:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                        pass
        except ImportError:
            pass

        # Clean up lock files
        try:
            lock_file = self.code_graph_path / "index.lock"
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass

        # Small delay to let OS release locks
        import time

        time.sleep(0.5)

    def _is_db_locked(self) -> bool:
        """Check if the SQLite DB is currently locked."""
        db_path = self.code_graph_path / "index.db"
        if not db_path.exists():
            return False

        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path), timeout=1)
            conn.execute("SELECT 1")
            conn.close()
            return False
        except sqlite3.OperationalError:
            return True

    def _ensure_call_edges(self) -> None:
        """Ensure Python call edges exist in the database.

        code-graph-mcp's incremental indexing deletes files which CASCADE deletes
        our custom 'calls' edges. This method detects missing edges and re-runs
        the Python call indexer to restore them.
        """
        import time

        # Only check once per minute to avoid redundant work
        now = time.time()
        if now - self._last_call_edge_check < 60:
            return
        self._last_call_edge_check = now

        db_path = self.code_graph_path / "index.db"
        if not db_path.exists():
            return

        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Check if we have any call edges
            cursor.execute("SELECT COUNT(*) FROM edges WHERE relation = 'calls'")
            count = cursor.fetchone()[0]
            conn.close()

            # If no call edges, re-run the Python call indexer
            if count == 0:
                print("[Trellis] Call edges missing, rebuilding...")
                try:
                    from .python_call_indexer import PythonCallGraphIndexer

                    indexer = PythonCallGraphIndexer(str(self.project_path))
                    indexer.index_calls()
                    print("[Trellis] Call edges restored")
                except Exception as e:
                    print(f"[Trellis] Warning: Could not restore call edges: {e}")
        except Exception:
            pass

    def _call(self, tool_name: str, **arguments) -> Union[Dict, List, str]:
        """Call an MCP tool via JSON-RPC.

        Args:
            tool_name: Name of the tool (e.g., "impact_analysis")
            **arguments: Tool arguments

        Returns:
            Tool result (dict, list, or string)

        Raises:
            RuntimeError: If process communication fails or times out
        """
        self._ensure_running()

        with self._lock:
            self._req_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }

            # Send request
            request_line = json.dumps(request) + "\n"
            self._proc.stdin.write(request_line)
            self._proc.stdin.flush()

            # Read response with timeout to prevent infinite hangs
            # Use threading approach (cross-platform, works with pipes on Windows)
            import threading

            timeout = 30  # Wait up to 30 seconds for response
            result = {"line": None, "error": None}

            def read_line():
                try:
                    result["line"] = self._proc.stdout.readline()
                except Exception as e:
                    result["error"] = e

            thread = threading.Thread(target=read_line)
            thread.daemon = True
            thread.start()
            thread.join(timeout)

            if thread.is_alive():
                self.close()
                raise RuntimeError(
                    f"Tool '{tool_name}' timed out after {timeout}s. "
                    "The code-graph-mcp process may be hung. Try restarting."
                )

            if result["error"]:
                raise result["error"]

            response_line = result["line"]

            if not response_line:
                raise RuntimeError("code-graph-mcp process closed unexpectedly")

            response = json.loads(response_line)

            if "error" in response:
                error = response["error"]
                raise RuntimeError(
                    f"Tool '{tool_name}' failed: {error.get('message', 'Unknown error')}"
                )

            # Parse result
            result = response.get("result", {})

            # Handle text content (MCP content array)
            if "content" in result:
                content = result["content"]
                if content and content[0].get("type") == "text":
                    text = content[0].get("text", "")
                    # Try to parse as JSON
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text

            return result

    def close(self) -> None:
        """Clean up subprocess and release file locks."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=1)
                except Exception:
                    pass
            self._proc = None

        # Clean up lock files to prevent conflicts
        try:
            lock_file = self.code_graph_path / "index.lock"
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ------------------------------------------------------------------
    # High-level API
    # ------------------------------------------------------------------

    def _normalize_result(self, result: Any) -> Dict[str, Any]:
        """Normalize MCP result to dict format."""
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"results": result}
        if isinstance(result, str):
            return {"text": result}
        return {}

    def _cli_parse(self, parser, *cli_args) -> Any:
        """Run a CLI command and parse its stdout."""
        result = self._run_cli(*cli_args)
        if not result.get("success"):
            return {}
        return parser(result["stdout"])

    def _mcp_then_cli(
        self,
        tool_name: str,
        mcp_args: Dict[str, Any],
        cli_args: List[str],
        parser,
    ) -> Any:
        """Try MCP tool; if it returns empty/fails, fall back to CLI parser."""
        try:
            result = self._call(tool_name, **mcp_args)
            normalized = self._normalize_result(result)
            if normalized and normalized != {}:
                return normalized
        except Exception:
            pass

        return self._cli_parse(parser, *cli_args)

    def analyze_impact(
        self, symbol: str, depth: int = 3, file_path: str = None
    ) -> Dict[str, Any]:
        """Analyze impact of changing a symbol.

        Uses the CLI `impact` command because the Rust MCP server does not
        expose an impact-analysis tool. If the symbol is ambiguous and no
        file_path is provided, resolves to the most "core" definition by
        counting synthetic incoming usage edges in the graph.
        """
        cli_args = ["impact", symbol, "--depth", str(depth)]
        if file_path:
            cli_args.extend(["--file", file_path])
            return self._cli_parse(cli_parsers.parse_impact, *cli_args)

        result = self._run_cli(*cli_args)
        if result.get("success"):
            return cli_parsers.parse_impact(result["stdout"])

        # Ambiguous symbol: pick the file with the most incoming usage edges
        resolved = self._resolve_ambiguous_symbol(symbol)
        if resolved:
            cli_args = ["impact", symbol, "--depth", str(depth), "--file", resolved]
            return self._cli_parse(cli_parsers.parse_impact, *cli_args)

        return {}

    def _resolve_ambiguous_symbol(self, symbol: str) -> Optional[str]:
        """Pick the most central definition file for an ambiguous symbol.

        Supports qualified names like "ClassName.method" or "dir/file.symbol".
        Heuristic: query the DB for all nodes with this name and choose the
        file whose node has the most incoming `calls`/`imports`/`implements`
        edges. Falls back to path substring matching for qualified names.
        """
        import sqlite3

        db_path = self.code_graph_path / "index.db"
        if not db_path.exists():
            return None

        # Extract base symbol name from qualified forms
        base_symbol = symbol
        path_hint = ""
        if "." in symbol:
            # Could be "ClassName.method" or "path/file.method"
            parts = symbol.rsplit(".", 1)
            base_symbol = parts[-1]
            path_hint = parts[0].replace(".", "/")
        elif "/" in symbol:
            parts = symbol.rsplit("/", 1)
            base_symbol = parts[-1]
            path_hint = parts[0]

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT f.path, n.id,
                       (SELECT COUNT(*) FROM edges WHERE target_id = n.id
                        AND relation IN ('calls', 'imports', 'implements')) as score
                FROM nodes n
                JOIN files f ON f.id = n.file_id
                WHERE n.name = ?
                  AND n.type IN ('function', 'method', 'class')
                  AND f.path NOT LIKE '%/tests/%'
                  AND f.path NOT LIKE '%/test_%'
                  AND f.path NOT LIKE '%.spec.%'
                  AND f.path NOT LIKE '%.test.%'
                ORDER BY
                    CASE WHEN ? != '' AND f.path LIKE '%' || ? || '%' THEN 0 ELSE 1 END,
                    score DESC,
                    n.start_line ASC
                LIMIT 1
                """,
                (base_symbol, path_hint, path_hint),
            )
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def search(
        self, query: str, language: str = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Semantic code search.

        Falls back to FTS5 via ast-search when no embedding model is available.
        """
        args = {"query": query, "limit": limit}
        if language:
            args["language"] = language

        result = self._call("semantic_code_search", **args)
        if isinstance(result, list) and result:
            return result
        if isinstance(result, dict) and result.get("results"):
            return result["results"]

        # Offline fallback: use CLI ast-search, which queries nodes_fts
        return self._cli_parse(
            cli_parsers.parse_ast_search,
            "ast-search",
            query,
            "--limit",
            str(limit),
        )

    def get_call_graph(
        self,
        symbol: str,
        direction: str = "both",
        depth: int = 2,
        file_path: str = None,
    ) -> Dict[str, Any]:
        """Get call graph for a symbol.

        Falls back to the CLI `callgraph` command because the MCP tool often
        returns empty results for JavaScript projects with dynamic dispatch.
        """
        cli_args = [
            "callgraph",
            symbol,
            "--direction",
            direction,
            "--depth",
            str(depth),
        ]
        if file_path:
            cli_args.extend(["--file", file_path])

        return self._mcp_then_cli(
            "get_call_graph",
            {"symbol_name": symbol, "direction": direction, "depth": depth},
            cli_args,
            cli_parsers.parse_callgraph,
        )

    def get_ast_node(
        self, symbol: str, include_source: bool = True, file_path: str = None
    ) -> Dict[str, Any]:
        """Get detailed info about a symbol."""
        cli_args = ["show", symbol]
        if file_path:
            cli_args.extend(["--file", file_path])
        if not include_source:
            cli_args.append("--compact")

        return self._mcp_then_cli(
            "get_ast_node",
            {"symbol_name": symbol, "include_source": include_source},
            cli_args,
            cli_parsers.parse_show,
        )

    def find_references(
        self,
        symbol: str,
        include_tests: bool = True,
        file_path: str = None,
    ) -> List[Dict[str, Any]]:
        """Find all references to a symbol."""
        cli_args = ["refs", symbol]
        if file_path:
            cli_args.extend(["--file", file_path])
        if include_tests:
            cli_args.append("--include-tests")

        def _parser(stdout: str) -> List[Dict[str, Any]]:
            return cli_parsers.parse_refs(stdout)

        return self._mcp_then_cli(
            "find_references",
            {"symbol_name": symbol, "include_tests": include_tests},
            cli_args,
            _parser,
        )

    def project_map(self) -> Dict[str, Any]:
        """Get full project architecture overview."""
        return self._mcp_then_cli("project_map", {}, ["map"], cli_parsers.parse_map)

    def module_overview(self, module_path: str) -> Dict[str, Any]:
        """Get overview of a specific module."""
        return self._mcp_then_cli(
            "module_overview",
            {"module_path": module_path},
            ["overview", module_path],
            cli_parsers.parse_overview,
        )

    def trace_http_route(self, route: str) -> Dict[str, Any]:
        """Trace HTTP route to handler and downstream calls."""
        return self._cli_parse(cli_parsers.parse_trace, "trace", route)

    def find_dead_code(self, path: str = None) -> List[Dict[str, Any]]:
        """Find unused code, filtering out constructors of inherited base classes."""
        cli_args = ["dead-code"]
        if path:
            cli_args.append(path)

        results = self._cli_parse(cli_parsers.parse_dead_code, *cli_args)
        if not results:
            return results

        return self._filter_abstract_constructors(results)

    def _filter_abstract_constructors(
        self, dead_entries: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Remove constructors of classes that are inherited by others.

        Abstract base classes (e.g. Submenu, Component, Panel, DrawingMode)
        are never directly instantiated but are still live through subclasses.
        """
        import sqlite3

        inherited_classes: set[str] = set()
        db_path = self.code_graph_path / "index.db"
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute(
                """
                SELECT DISTINCT tgt.name
                FROM edges e
                JOIN nodes tgt ON tgt.id = e.target_id
                WHERE e.relation = 'inherits'
                """
            )
            inherited_classes = {row[0] for row in c.fetchall()}
            conn.close()
        except Exception:
            pass

        if not inherited_classes:
            return dead_entries

        def _class_name_for_constructor(entry: Dict[str, Any]) -> Optional[str]:
            if entry.get("name") != "constructor" or entry.get("kind") != "method":
                return None
            loc = entry.get("location", {})
            file_path = loc.get("file_path")
            start_line = loc.get("start_line")
            if not file_path or start_line is None:
                return None

            try:
                conn = sqlite3.connect(str(db_path))
                c = conn.cursor()
                c.execute(
                    """
                    SELECT n.name
                    FROM nodes n
                    JOIN files f ON f.id = n.file_id
                    WHERE n.type = 'class'
                        AND f.path = ?
                        AND n.start_line <= ?
                        AND (n.end_line IS NULL OR n.end_line >= ?)
                    LIMIT 1
                    """,
                    (file_path, start_line, start_line),
                )
                row = c.fetchone()
                conn.close()
                return row[0] if row else None
            except Exception:
                return None

        filtered: List[Dict[str, Any]] = []
        for entry in dead_entries:
            class_name = _class_name_for_constructor(entry)
            if class_name and class_name in inherited_classes:
                continue
            filtered.append(entry)

        return filtered

    def dependency_graph(
        self, file_path: str, direction: str = "incoming"
    ) -> Dict[str, Any]:
        """Get dependency graph for a file."""
        return self._cli_parse(
            cli_parsers.parse_deps,
            "deps",
            file_path,
            "--direction",
            direction,
        )

    def health_check(self) -> Dict[str, Any]:
        """Check index status and health."""
        result = self._call("get_index_status")
        return self._normalize_result(result)

    def _run_cli(self, *args, timeout: int = 300) -> Dict[str, Any]:
        """Run code-graph-mcp CLI command.

        Used for operations that can't be done via MCP (like indexing).

        Args:
            *args: CLI arguments
            timeout: Max seconds to wait

        Returns:
            Dict with stdout, stderr, returncode
        """
        import subprocess
        import time

        # Close any existing process and wait for file locks to release
        self.close()
        time.sleep(0.5)  # Give OS time to release file locks

        cmd = [str(self.binary_path)] + list(args)
        env = os.environ.copy()

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=str(self.project_path),
            timeout=timeout,
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }

    def sync_project(self) -> Dict[str, Any]:
        """Sync/index the project codebase.

        Triggers code-graph-mcp to index all source files.
        Runs 'rebuild-index --confirm' CLI command.
        """
        # Close any running MCP server to avoid DB lock conflicts
        self.close()

        result = self._run_cli("rebuild-index", "--confirm")

        if result["success"]:
            # Also run python call indexer to add call edges
            try:
                from .python_call_indexer import PythonCallGraphIndexer

                indexer = PythonCallGraphIndexer(str(self.project_path))
                call_result = indexer.index_calls()
                result["call_indexer"] = call_result
            except Exception as e:
                import traceback

                result["call_indexer_error"] = str(e)
                result["call_indexer_traceback"] = traceback.format_exc()

            # Augment JS dynamic-dispatch patterns (commandFactory, getComponent, new)
            try:
                from .js_graph_augmentor import JSGraphAugmentor

                augmentor = JSGraphAugmentor(
                    str(self.project_path), str(self.code_graph_path / "index.db")
                )
                result["js_augmentor"] = augmentor.augment()
            except Exception as e:
                import traceback

                result["js_augmentor_error"] = str(e)
                result["js_augmentor_traceback"] = traceback.format_exc()

        return result

    def incremental_sync(self) -> Dict[str, Any]:
        """Run incremental index update.

        Only indexes changed files since last sync.
        Runs 'incremental-index' CLI command.
        """
        # Close any running MCP server to avoid DB lock conflicts
        self.close()

        result = self._run_cli("incremental-index")

        if result["success"]:
            # Re-run JS augmentor so dynamic-dispatch edges stay in sync
            try:
                from .js_graph_augmentor import JSGraphAugmentor

                augmentor = JSGraphAugmentor(
                    str(self.project_path), str(self.code_graph_path / "index.db")
                )
                result["js_augmentor"] = augmentor.augment()
            except Exception as e:
                import traceback

                result["js_augmentor_error"] = str(e)
                result["js_augmentor_traceback"] = traceback.format_exc()

        return result

    # ------------------------------------------------------------------
    # Visualizer API (converts to our format)
    # ------------------------------------------------------------------

    def get_graph_for_visualizer(self, max_nodes: int = 2000) -> Dict[str, Any]:
        """Get graph data formatted for the visualizer.

        Returns ALL functions for the project (up to max_nodes).
        Queries SQLite directly for performance.
        """
        return self._get_full_graph()

    def _get_full_graph(self) -> Dict[str, Any]:
        """Get full detailed graph for small repos.

        Queries SQLite directly to bypass MCP token limits.
        """
        import sqlite3

        # Get project map for structure
        pmap = self.project_map()

        nodes = []
        links = []

        # Add module nodes as features
        modules = pmap.get("modules", [])
        for mod in modules:
            mod_path = mod.get("path", "")
            nodes.append(
                {
                    "id": f"mod:{mod_path}",
                    "type": "module",
                    "label": mod_path,
                    "name": mod_path,
                }
            )

        # Query SQLite directly for all functions (bypasses MCP token limits)
        db_path = self.code_graph_path / "index.db"
        all_funcs = []
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT n.name, n.qualified_name, f.path, n.start_line, n.end_line, n.type, n.signature
                    FROM nodes n
                    JOIN files f ON n.file_id = f.id
                    WHERE n.type IN ('function', 'method')
                      AND n.name NOT LIKE 'test_%'
                      AND n.name != '__init__'
                      AND n.name != '<module>'
                    ORDER BY f.path, n.start_line
                """)
                all_funcs = [
                    {
                        "name": row[0],
                        "qualified_name": row[1] or row[0],
                        "file_path": row[2],
                        "start_line": row[3],
                        "end_line": row[4],
                        "type": row[5],
                        "signature": row[6],
                    }
                    for row in cursor.fetchall()
                ]
                conn.close()
            except Exception:
                # Fall back to MCP tool if DB query fails
                search_result = self._call("ast_search", type="fn", limit=100)
                if isinstance(search_result, dict):
                    all_funcs = search_result.get("results", [])
                elif isinstance(search_result, list):
                    all_funcs = search_result

        for func in all_funcs:
            func_id = func.get("qualified_name") or func.get("name", "unknown")
            file_path = func.get("file_path", "")

            nodes.append(
                {
                    "id": f"func:{func_id}",
                    "type": "function",
                    "label": func.get("name", func_id),
                    "name": func.get("name", ""),
                    "file_path": file_path,
                    "line": func.get("start_line", 0),
                    "kind": func.get("type", "function"),
                    "signature": func.get("signature", ""),
                }
            )

            # Link to module
            if file_path:
                if "/" not in file_path:
                    links.append(
                        {
                            "source": f"func:{func_id}",
                            "target": "mod:<root>",
                            "type": "belongs_to",
                        }
                    )
                else:
                    best_mod = None
                    best_len = 0
                    for mod in modules:
                        mod_path = mod.get("path", "")
                        if (
                            mod_path
                            and mod_path != "<root>"
                            and file_path.startswith(mod_path + "/")
                        ):
                            if len(mod_path) > best_len:
                                best_len = len(mod_path)
                                best_mod = mod_path
                    if best_mod:
                        links.append(
                            {
                                "source": f"func:{func_id}",
                                "target": f"mod:{best_mod}",
                                "type": "belongs_to",
                            }
                        )

        return {
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_nodes": len(nodes),
                "total_functions": len([n for n in nodes if n["type"] == "function"]),
                "total_modules": len([n for n in nodes if n["type"] == "module"]),
                "total_links": len(links),
            },
            "view_mode": "full",
        }

    def _get_simplified_graph(self, health: Dict) -> Dict[str, Any]:
        """Get simplified graph for large repos (>200 functions).

        Shows modules + top functions by caller count.
        Queries SQLite directly to get actual function data.
        """
        import sqlite3

        pmap = self.project_map()

        nodes = []
        links = []

        # Add module nodes
        modules = pmap.get("modules", [])
        for mod in modules:
            mod_path = mod.get("path", "")
            symbol_count = mod.get("symbol_count", 0)
            nodes.append(
                {
                    "id": f"mod:{mod_path}",
                    "type": "module",
                    "label": mod_path,
                    "name": mod_path,
                    "symbol_count": symbol_count,
                    "is_module": True,
                }
            )

        # Add module dependency links
        deps = pmap.get("module_dependencies", [])
        for dep in deps:
            from_mod = dep.get("from", "")
            to_mod = dep.get("to", "")
            if from_mod and to_mod:
                links.append(
                    {
                        "source": f"mod:{from_mod}",
                        "target": f"mod:{to_mod}",
                        "type": "depends_on",
                    }
                )

        # Query SQLite for representative functions from different files
        db_path = self.code_graph_path / "index.db"
        hot_funcs = []
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                # Get one function per file to show diversity
                cursor.execute("""
                    SELECT 
                        n.name,
                        n.qualified_name,
                        f.path as file_path,
                        n.signature,
                        0 as caller_count
                    FROM nodes n
                    JOIN files f ON n.file_id = f.id
                    WHERE n.type IN ('function', 'method')
                      AND n.name NOT LIKE 'test_%'
                      AND n.name != '__init__'
                      AND n.name != '<module>'
                    GROUP BY f.path
                    ORDER BY f.path
                    LIMIT 30
                """)
                hot_funcs = [
                    {
                        "name": row[0],
                        "qualified_name": row[1] or row[0],
                        "file_path": row[2],
                        "signature": row[3],
                        "caller_count": row[4],
                    }
                    for row in cursor.fetchall()
                ]
                conn.close()
            except Exception:
                pass

        # Fallback to project_map hot_functions if DB query fails
        if not hot_funcs:
            hot_funcs = pmap.get("hot_functions", [])[:20]

        for func in hot_funcs:
            func_name = func.get("name", "")
            func_id = func.get("qualified_name", func_name)
            if not func_id:
                continue

            nodes.append(
                {
                    "id": f"func:{func_id}",
                    "type": "function",
                    "label": func_name,
                    "name": func_name,
                    "file_path": func.get("file_path", ""),
                    "signature": func.get("signature", ""),
                    "caller_count": func.get("caller_count", 0),
                    "is_hot": True,
                }
            )

            # Link to module
            file_path = func.get("file_path", "")
            if file_path:
                if "/" not in file_path:
                    links.append(
                        {
                            "source": f"func:{func_id}",
                            "target": "mod:<root>",
                            "type": "belongs_to",
                        }
                    )
                else:
                    best_mod = None
                    best_len = 0
                    for mod in modules:
                        mod_path = mod.get("path", "")
                        if (
                            mod_path
                            and mod_path != "<root>"
                            and file_path.startswith(mod_path + "/")
                        ):
                            if len(mod_path) > best_len:
                                best_len = len(mod_path)
                                best_mod = mod_path
                    if best_mod:
                        links.append(
                            {
                                "source": f"func:{func_id}",
                                "target": f"mod:{best_mod}",
                                "type": "belongs_to",
                            }
                        )

        return {
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_nodes": health.get("nodes_count", 0),
                "total_functions": health.get("nodes_count", 0),
                "total_features": len(modules),
                "total_links": len(links),
                "shown_nodes": len(nodes),
                "shown_functions": len([n for n in nodes if n["type"] == "function"]),
                "note": f"Simplified view: {len(hot_funcs)} of ~{health.get('nodes_count', 0)} functions shown",
            },
            "view_mode": "simplified",
        }

    def get_impact_graph(self, symbol: str, depth: int = 2) -> Dict[str, Any]:
        """Get impact analysis formatted for visualizer.

        Returns:
            {
                "nodes": [...],
                "links": [...],
                "stats": {...},
                "root_function": "...",
                "risk_level": "..."
            }
        """
        impact = self.analyze_impact(symbol, depth=depth)

        nodes = []
        links = []

        # Root node
        root_id = f"func:{symbol}"
        nodes.append(
            {
                "id": root_id,
                "type": "function",
                "label": symbol,
                "name": symbol,
                "is_root": True,
                "risk_level": impact.get("risk_level", "unknown"),
            }
        )

        # Add affected functions
        affected = impact.get("affected_functions", [])
        for func in affected:
            func_name = func.get("name", func.get("qualified_name", "unknown"))
            func_id = f"func:{func_name}"

            nodes.append(
                {
                    "id": func_id,
                    "type": "function",
                    "label": func_name,
                    "name": func_name,
                    "file_path": func.get("file_path", ""),
                    "confidence": func.get("confidence", 0),
                    "impact_type": func.get("impact_type", "affected"),
                }
            )

            links.append(
                {
                    "source": root_id,
                    "target": func_id,
                    "type": "impacts",
                    "confidence": func.get("confidence", 0),
                }
            )

        return {
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_nodes": len(nodes),
                "total_links": len(links),
                "risk_level": impact.get("risk_level", "unknown"),
                "affected_count": len(affected),
            },
            "root_function": symbol,
            "risk_level": impact.get("risk_level", "unknown"),
            "view_mode": "impact",
        }

    # ------------------------------------------------------------------
    # Feature Impact Analysis
    # ------------------------------------------------------------------

    def get_feature_impact(self, symbol: str, depth: int = 2) -> Dict[str, Any]:
        """Get comprehensive impact analysis with affected functions.

        Uses enhanced analyzer that builds affected functions from:
        - Symbol references (find_references)
        - File-level function discovery (SQLite)
        - Feature mapping (project.md)
        - Cross-feature dependencies

        Returns:
            Impact report with affected functions, features, divergence, pointers
        """
        # Ensure call edges exist (they may have been wiped by code-graph-mcp indexing)
        self._ensure_call_edges()

        from .impact_analyzer import ImpactAnalyzer

        analyzer = ImpactAnalyzer(self, str(self.project_path))
        return analyzer.analyze_impact(symbol, depth=depth)

    def get_feature_context(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get feature context for a function.

        Returns:
            Feature details, decisions, constraints
        """
        from .feature_impact import FeatureImpactAnalyzer

        analyzer = FeatureImpactAnalyzer(self, str(self.project_path))
        return analyzer.get_feature_context(symbol)

    def get_development_pointers(self, symbol: str) -> List[str]:
        """Get development pointers for a function.

        Returns actionable insights for coding agent.
        """
        from .feature_impact import FeatureImpactAnalyzer

        analyzer = FeatureImpactAnalyzer(self, str(self.project_path))
        return analyzer.get_development_pointers(symbol)

    def check_feature_divergence(self, symbol: str) -> List[str]:
        """Check if function diverges from feature spec.

        Returns:
            List of divergence warnings
        """
        from .feature_impact import FeatureImpactAnalyzer

        analyzer = FeatureImpactAnalyzer(self, str(self.project_path))
        return analyzer.check_divergence(symbol)

    def get_feature_functions(self, feature_name: str) -> List[Dict[str, Any]]:
        """Find all functions that belong to a feature defined in project.md.

        Uses file patterns from project.md to match source files, then returns
        every function/method in those files.
        """
        from .feature_impact import ProjectContextParser

        context = ProjectContextParser(str(self.project_path))
        feature = context.features.get(feature_name)
        if not feature:
            return []

        if not feature.file_patterns:
            return []

        import sqlite3
        from .utils import resolve_code_graph_db

        db_path = resolve_code_graph_db(str(self.project_path))
        if not db_path.exists():
            return []

        functions = []
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            for pattern in feature.file_patterns:
                # Convert glob-ish pattern to SQL LIKE
                like_pattern = pattern.replace("**", "%").replace("*", "%")
                cursor.execute(
                    """
                    SELECT n.name, n.qualified_name, f.path, n.start_line, n.end_line, n.type
                    FROM nodes n
                    JOIN files f ON n.file_id = f.id
                    WHERE f.path LIKE ?
                      AND n.type IN ('function', 'method')
                    ORDER BY f.path, n.start_line
                """,
                    (like_pattern,),
                )

                for row in cursor.fetchall():
                    functions.append(
                        {
                            "name": row[0],
                            "qualified_name": row[1] or row[0],
                            "file_path": row[2],
                            "start_line": row[3],
                            "end_line": row[4],
                            "type": row[5],
                        }
                    )

            conn.close()
        except Exception:
            return []

        # Deduplicate by qualified name
        seen = set()
        unique = []
        for func in functions:
            key = func["qualified_name"]
            if key not in seen:
                seen.add(key)
                unique.append(func)

        return unique

    def search_features(self, query: str) -> List[Dict[str, Any]]:
        """Search features defined in project.md by name or description."""
        from .feature_impact import ProjectContextParser

        context = ProjectContextParser(str(self.project_path))
        query_lower = query.lower()
        results = []

        for name, feature in context.features.items():
            if (
                query_lower in name.lower()
                or query_lower in feature.description.lower()
            ):
                results.append(
                    {
                        "feature_name": name,
                        "description": feature.description,
                        "status": feature.status,
                        "owner": feature.owner,
                        "file_patterns": feature.file_patterns,
                        "dependencies": feature.dependencies,
                    }
                )

        return results

    def get_feature_info(self, feature_name: str) -> Dict[str, Any]:
        """Get comprehensive information about a feature.

        Combines project.md spec, related knowledge notes, functions in the
        feature, and an aggregate impact summary.
        """
        from .feature_impact import ProjectContextParser
        from .knowledge_graph import NoteGraph

        context = ProjectContextParser(str(self.project_path))
        feature = context.features.get(feature_name)

        note_graph = NoteGraph(str(self.project_path))
        note = None
        note_id_candidates = [
            feature_name,
            f"feature-{feature_name.lower().replace(' ', '-')}",
        ]
        for candidate in note_id_candidates:
            note = note_graph.get_note(candidate)
            if note:
                break

        # Also try alias resolution via title
        if not note:
            for n in note_graph.notes.values():
                if n.title.lower() == feature_name.lower() or n.title.lower().endswith(
                    feature_name.lower()
                ):
                    note = n
                    break

        functions = self.get_feature_functions(feature_name)

        # Aggregate impact: get top-level impacted symbols by centrality
        hot_functions = sorted(
            functions,
            key=lambda f: len(self._get_caller_symbols(f["qualified_name"])),
            reverse=True,
        )[:10]

        result: Dict[str, Any] = {
            "feature_name": feature_name,
            "found_in_spec": feature is not None,
            "description": feature.description if feature else "",
            "status": feature.status if feature else "",
            "owner": feature.owner if feature else "",
            "file_patterns": feature.file_patterns if feature else [],
            "dependencies": feature.dependencies if feature else [],
            "decisions": [],
            "constraints": feature.constraints if feature else [],
            "note": None,
            "functions_count": len(functions),
            "functions_sample": functions[:20],
            "hot_functions": hot_functions,
            "related_notes": [],
        }

        if feature:
            result["decisions"] = [
                {
                    "id": d.decision_id,
                    "description": d.description,
                    "rationale": d.rationale,
                    "constraints": d.constraints,
                }
                for d in feature.decisions
            ]

        if note:
            result["note"] = {
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "tags": note.tags,
                "links": note.links,
                "mentions": note.mentions,
                "backlinks": note_graph.get_backlinks(note.id),
                "related_notes": note_graph.get_related_notes(note.id),
            }
            result["related_notes"] = note_graph.get_related_notes(note.id)

        return result

    def _get_caller_symbols(self, symbol: str) -> List[str]:
        """Return names of symbols that call the given symbol."""
        import sqlite3
        from .utils import resolve_code_graph_db

        db_path = resolve_code_graph_db(str(self.project_path))
        if not db_path.exists():
            return []

        callers = []
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            simple_name = symbol.split(".")[-1]
            cursor.execute(
                """
                SELECT DISTINCT s.name
                FROM edges e
                JOIN nodes t ON e.target_id = t.id
                JOIN nodes s ON e.source_id = s.id
                WHERE e.relation = 'calls'
                  AND (t.name = ? OR t.qualified_name = ?)
            """,
                (simple_name, symbol),
            )
            callers = [row[0] for row in cursor.fetchall()]
            conn.close()
        except Exception:
            pass

        return callers

    def find_feature_for_function(self, function_path: str) -> Optional[str]:
        """Find which project.md feature owns a source file path."""
        from .feature_impact import ProjectContextParser

        context = ProjectContextParser(str(self.project_path))
        feature = context.get_feature_for_file(function_path)
        return feature.feature_name if feature else None
