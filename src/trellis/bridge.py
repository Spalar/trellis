"""Python bridge to code-graph-mcp binary.

Provides a Pythonic API over the code-graph-mcp JSON-RPC interface.
Auto-detects and manages the binary lifecycle.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import cli_parsers
from .utils import get_code_graph_path, has_column


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
        # The file watcher lives in the server process, so it must be (re)armed
        # after every spawn. `start_watch` is idempotent upstream.
        self._watch_started = False

        # Setup .code-graph in trellis data directory with symlink in project
        self.code_graph_path = get_code_graph_path(self.project_path)

        # (path -> indexed_at) seen when Trellis's custom edges were last linked;
        # loaded lazily from EDGE_STATE_FILE so restarts don't re-link.
        self._edge_snapshot: Optional[Dict[str, Any]] = None
        self._edge_fingerprint: Optional[tuple] = None  # see _fingerprint
        self._failed_kinds: set = set()  # failed re-links, retried after a back-off
        self._retry_at: float = 0.0
        self._django_cache: Optional[tuple] = None  # (key, routes)
        self._py_indexer = None  # keeps its parse cache between re-links
        # Serializes re-linking and route building: both use _py_indexer and
        # tool calls run on parallel threads.
        self._edge_lock = threading.RLock()

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

            # The bridge always spawns deliberately for self.project_path — an
            # explicit trellis_sync target, never an accidental throwaway cwd.
            # Upstream's non-project guard would otherwise serve a 0-tool stub
            # for markerless dirs (e.g. a workspace with no .git/package.json),
            # breaking every RPC-backed tool. Force the real server; it creates
            # .code-graph under the project root as usual.
            env = os.environ.copy()
            env["CODE_GRAPH_FORCE_PLUGIN_MCP"] = "1"

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
            self._record_server_pid(self._proc.pid)

    def _pidfile(self) -> Path:
        return self.code_graph_path / "trellis-server.pids"

    def _record_server_pid(self, pid: int) -> None:
        """Remember a spawned server PID so zombie cleanup only kills ours."""
        try:
            self.code_graph_path.mkdir(parents=True, exist_ok=True)
            pids = set(self._read_server_pids())
            pids.add(pid)
            self._pidfile().write_text("\n".join(str(p) for p in sorted(pids)))
        except Exception:
            pass

    def _read_server_pids(self) -> List[int]:
        try:
            return [
                int(line)
                for line in self._pidfile().read_text().splitlines()
                if line.strip().isdigit()
            ]
        except Exception:
            return []

    def _pid_is_code_graph(self, pid: int) -> bool:
        """True when PID is alive and is a code-graph-mcp process."""
        try:
            if os.name == "nt":
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                ).stdout
                return "code-graph-mcp" in out
            with open(f"/proc/{pid}/comm") as f:
                return "code-graph-mcp" in f.read()
        except Exception:
            return False

    def _kill_pid(self, pid: int) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                import signal

                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    def _kill_zombie_processes(self) -> None:
        """Kill leftover code-graph-mcp processes RECORDED FOR THIS PROJECT.

        Only PIDs this bridge (or a previous bridge for the same project, via
        the .code-graph pidfile) spawned are considered, and each is verified
        to still be a code-graph-mcp process before killing. Never touches
        other projects' servers or unrelated processes.
        """
        own_pid = self._proc.pid if (self._proc and self._proc.poll() is None) else None
        for pid in self._read_server_pids():
            if pid != own_pid and self._pid_is_code_graph(pid):
                self._kill_pid(pid)

        # Killed PIDs are gone and any other recorded PID is no longer a
        # code-graph process: only our own live child stays recorded.
        try:
            if own_pid:
                self._pidfile().write_text(str(own_pid))
            else:
                self._pidfile().unlink(missing_ok=True)
        except Exception:
            pass

        # index.lock is an OS handle lock released on process death; deleting
        # it could break mutual exclusion with a live process (see _close_locked).

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

    EDGE_STATE_FILE = "trellis-edge-state.json"
    _RETRY_SECONDS = 60  # back-off after a failed re-link

    def ensure_fresh(self) -> None:
        """Bring the index up to date with the working tree before a query.

        Upstream re-indexes edited files on its next tool call, so poke it
        with a cheap one, then re-link Trellis's custom edges for whatever
        it re-indexed. Never raises: a stale answer beats no answer.
        """
        try:
            self._call("ast_search", query="__trellis_refresh__", limit=1)
        except Exception:
            pass
        self._ensure_call_edges()

    def _index_snapshot(self, db_path: Path) -> Dict[str, str]:
        """{"py": digest, "js": digest} over each file's (path, indexed_at, top node id).

        indexed_at has one-second resolution; re-indexing also re-inserts
        the file's nodes under new ids, so a digest changes on every pass
        over a file of that language. Older schemas use on-disk mtimes.
        """
        from . import graph_queries as gq

        with gq.connect(db_path) as conn:
            if has_column(conn, "files", "indexed_at"):
                rows = conn.execute(
                    """
                    SELECT f.path, f.indexed_at, MAX(n.id)
                    FROM files f LEFT JOIN nodes n ON n.file_id = f.id
                    GROUP BY f.id ORDER BY f.path
                    """
                ).fetchall()
            else:
                rows = []
                for (path,) in conn.execute("SELECT path FROM files ORDER BY path"):
                    try:
                        mtime = (self.project_path / path).stat().st_mtime_ns
                    except OSError:
                        mtime = None
                    rows.append((path, mtime, None))
        digests = {"py": hashlib.sha1(), "js": hashlib.sha1()}
        for path, stamp, top in rows:
            kind = "py" if path.endswith(".py") else "js" if path.endswith(".js") else None
            if kind:
                digests[kind].update(f"{path}\0{stamp}\0{top}\n".encode())
        return {kind: d.hexdigest() for kind, d in digests.items()}

    def _ensure_call_edges(self) -> None:
        """Re-link Trellis's custom edges after upstream re-indexed files.

        Upstream re-indexes a file by deleting and re-inserting its nodes,
        which CASCADE-deletes every edge touching them, including Trellis
        edges into that file from files that did not change. A one-row
        fingerprint short-circuits the common no-change case.
        """
        db_path = self.code_graph_path / "index.db"
        if not db_path.exists():
            return
        with self._edge_lock:
            self._relink_if_changed(db_path)

    def _fingerprint(self, db_path: Path) -> Optional[tuple]:
        """(files, newest indexed_at, top node id, upstream edges, Trellis edges).

        The first four change whenever upstream re-indexes anything; the last
        drops when a re-index deletes Trellis's edges.
        """
        from . import graph_queries as gq
        from .js_graph_augmentor import OWNED_META

        try:
            with gq.connect(db_path) as conn:
                stamp = "MAX(indexed_at)" if has_column(conn, "files", "indexed_at") else "0"
                files, newest, top, total = conn.execute(
                    f"SELECT (SELECT COUNT(*) FROM files), (SELECT {stamp} FROM files),"
                    " (SELECT MAX(id) FROM nodes), (SELECT COUNT(*) FROM edges)"
                ).fetchone()
                owned = conn.execute(
                    "SELECT COUNT(*) FROM edges"
                    " WHERE metadata LIKE '{\"caller\"%' OR metadata = ?",
                    (OWNED_META,),
                ).fetchone()[0]
            return (files, newest, top, total - owned, owned)
        except Exception:
            return None

    def _relink_if_changed(self, db_path: Path) -> None:
        import time

        before = self._fingerprint(db_path)
        retry = bool(self._failed_kinds) and time.time() >= self._retry_at
        if before is not None and before == self._edge_fingerprint and not retry:
            return  # nothing re-indexed since the last check
        try:
            snapshot = self._index_snapshot(db_path)
        except Exception:
            return
        if self._edge_snapshot is None:
            self._edge_snapshot = self._load_edge_state()
        previous = self._edge_snapshot
        kinds = {k for k in ("py", "js") if snapshot.get(k) != previous.get(k)}
        if not kinds and self._edge_fingerprint is not None and before != self._edge_fingerprint:
            # Same files and stamps, but the index changed under us (e.g. a
            # re-index within the same second dropped edges): re-link anyway.
            kinds = {"py", "js"}
        if retry:
            kinds |= self._failed_kinds
        else:
            kinds -= self._failed_kinds  # still backing off
        failed = self._heal_custom_edges(python="py" in kinds, js="js" in kinds) if kinds else set()
        self._failed_kinds = (self._failed_kinds - kinds) | failed
        if failed:
            self._retry_at = time.time() + self._RETRY_SECONDS
        after = self._fingerprint(db_path)
        # Upstream state as seen BEFORE re-linking (a re-index that lands
        # meanwhile still differs next time); Trellis edge count AFTER it.
        self._edge_fingerprint = before[:4] + after[4:] if before and after else None
        if snapshot != previous:
            self._edge_snapshot = snapshot
            self._save_edge_state(snapshot)

    def _load_edge_state(self) -> Dict[str, str]:
        try:
            state = json.loads(
                (self.code_graph_path / self.EDGE_STATE_FILE).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        # Older versions stored a per-file map: treat it as unknown so the
        # first check re-links once.
        return state if isinstance(state, dict) and set(state) <= {"py", "js"} else {}

    def _save_edge_state(self, snapshot: Dict[str, str]) -> None:
        try:
            (self.code_graph_path / self.EDGE_STATE_FILE).write_text(
                json.dumps(snapshot), encoding="utf-8"
            )
        except OSError:
            pass

    def _heal_custom_edges(self, python: bool = True, js: bool = True) -> set:
        """Re-run the custom-edge indexers; returns the kinds that failed ("py", "js")."""
        failed = set()
        if python:
            try:
                if self._py_indexer is None:
                    from .python_call_indexer import PythonCallGraphIndexer

                    self._py_indexer = PythonCallGraphIndexer(str(self.project_path))
                self._py_indexer.index_calls()
            except Exception as e:
                failed.add("py")
                print(f"[Trellis] Warning: Could not restore call edges: {e}", file=sys.stderr)
        if js:
            try:
                from .js_graph_augmentor import JSGraphAugmentor

                JSGraphAugmentor(
                    str(self.project_path), str(self.code_graph_path / "index.db")
                ).augment()
            except Exception as e:
                failed.add("js")
                print(f"[Trellis] Warning: Could not restore JS edges: {e}", file=sys.stderr)
        return failed

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
            # Arm the file watcher once per server process. Never let watcher
            # setup block real work (e.g. a secondary-mode server refuses it).
            if not self._watch_started:
                try:
                    self._rpc_locked("start_watch", timeout=30)
                    self._watch_started = True
                except Exception:
                    pass
            return self._rpc_locked(tool_name, **arguments)

    def _rpc_locked(
        self, tool_name: str, timeout: int = 120, **arguments
    ) -> Union[Dict, List, str]:
        """Send one JSON-RPC tools/call and return its parsed result.

        Caller must hold ``self._lock``. The server interleaves JSON-RPC
        notifications (progress/message) on stdout, so keep reading until a
        response matching this request's id arrives; skip everything else.
        """
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

        import threading

        def read_line():
            try:
                result["line"] = self._proc.stdout.readline()
            except Exception as e:
                result["error"] = e

        response = None
        while response is None:
            result = {"line": None, "error": None}
            thread = threading.Thread(target=read_line)
            thread.daemon = True
            thread.start()
            thread.join(timeout)

            if thread.is_alive():
                # Already holding self._lock here (callers: _call/_rpc_locked).
                self._close_locked()
                raise RuntimeError(
                    f"Tool '{tool_name}' timed out after {timeout}s. "
                    "The code-graph-mcp process may be hung. Try restarting."
                )

            if result["error"]:
                raise result["error"]

            response_line = result["line"]

            if not response_line:
                raise RuntimeError("code-graph-mcp process closed unexpectedly")

            try:
                candidate = json.loads(response_line)
            except json.JSONDecodeError:
                continue  # non-JSON noise on stdout: skip

            if candidate.get("id") != request["id"]:
                continue  # notification or stale response: skip

            response = candidate

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
        """Clean up subprocess and release file locks (thread-safe)."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        """Terminate the subprocess. Caller must hold ``self._lock``."""
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
            # The file watcher dies with the server process; re-arm on the
            # next spawn.
            self._watch_started = False

        # Note: .code-graph/index.lock is managed by code-graph-mcp via an OS
        # handle lock (flock / Windows exclusive handle). There is no stale
        # state to clean up — the OS releases on process death — and deleting
        # the lock file would break mutual exclusion between live processes.

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

    @staticmethod
    def _raise_if_building(result: Dict[str, Any]) -> None:
        """Turn the engine's 'index is still being built' refusal into an error.

        While the engine server (re)builds an index, e.g. once after an
        engine upgrade, CLI queries exit 1 with that message; returning an
        empty result instead reads as "no modules" or "no routes".
        """
        text = "\n".join((result.get("stdout") or "", result.get("stderr") or ""))
        if "still being built" in text:
            line = next(ln for ln in text.splitlines() if "still being built" in ln)
            raise RuntimeError(line.replace("[code-graph]", "").strip())

    def _cli_parse(self, parser, *cli_args) -> Any:
        """Run a CLI command and parse its stdout."""
        result = self._run_cli(*cli_args)
        if not result.get("success"):
            self._raise_if_building(result)
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

    def ast_search(
        self,
        query: str = None,
        node_type: str = None,
        returns: str = None,
        params: str = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Structural AST search with type/signature filters.

        Passthrough to the `ast_search` MCP tool — richer than the CLI
        ast-search because it accepts filters the CLI does not expose
        (node type, return type, parameter signature).

        At least one of query/node_type/returns/params is required upstream.
        """
        # With a query, the engine takes its top `limit` full-text hits and only
        # then drops test symbols, so a small limit can come back empty
        # (Django: query='apply_transition', limit=3 -> 0 results). Ask for a
        # wider pool and cut it here.
        args: Dict[str, Any] = {"limit": max(limit * 3, 30) if query else limit}
        if query:
            args["query"] = query
        if node_type:
            args["type"] = node_type
        if returns:
            args["returns"] = returns
        if params:
            args["params"] = params

        result = self._call("ast_search", **args)
        if isinstance(result, dict):
            results = result.get("results")
            if results is None and result:
                results = [result]
            result = results
        if not isinstance(result, list):
            return []
        if query:
            # Exact name matches first; full-text rank can put the symbol
            # itself below one that only mentions it.
            q = query.lower()
            result = sorted(
                result,
                key=lambda r: not isinstance(r, dict)
                or q not in {str(r.get("name", "")).lower(), str(r.get("name", "")).lower().rsplit(".", 1)[-1]},
            )
        return result[:limit]

    def trace_http_route(
        self,
        route_path: str,
        depth: int = 3,
        include_middleware: bool = True,
    ) -> Dict[str, Any]:
        """Trace an HTTP route to its handler and downstream calls.

        Django/DRF projects are traced by Trellis (django_routes); for other
        frameworks this uses the `find_http_route` MCP tool, falling back to
        the CLI `trace` command. An empty route_path lists Django routes.
        Projects with no indexed routes get an empty result, not an error.
        """
        django = self._trace_django_route(route_path, depth)
        if django is not None and (django.get("matches") or "routes" in django):
            return django
        upstream = self._mcp_then_cli(
            "find_http_route",
            {
                "route_path": route_path,
                "depth": depth,
                "include_middleware": include_middleware,
            },
            ["trace", route_path],
            cli_parsers.parse_trace,
        )
        if django is None:
            return upstream
        # No Django route matched: the project may also serve another
        # framework, so prefer upstream's answer and keep the Django hint.
        if isinstance(upstream, dict) and upstream and "error" not in upstream:
            upstream.setdefault("django", django)
            return upstream
        return django

    def _trace_django_route(self, route_path: str, depth: int) -> Optional[Dict[str, Any]]:
        """Django/DRF trace, or None when the project has no Django URLconfs.

        The route table is cached until a Python file or the index changes,
        including the empty table of a non-Django project.
        """
        from .python_call_indexer import PythonCallGraphIndexer

        db_path = self.code_graph_path / "index.db"
        if not db_path.exists():
            return None
        self.ensure_fresh()
        try:
            with self._edge_lock:
                if self._py_indexer is None:
                    self._py_indexer = PythonCallGraphIndexer(str(self.project_path))
                self._py_indexer.load()
                key = (self._py_indexer.stamp(), self._edge_fingerprint)
                cached = self._django_cache
                if cached and cached[0] == key:
                    routes = cached[1]
                else:
                    routes = self._build_django_routes(db_path)
                    self._django_cache = (key, routes)
        except Exception:
            return None  # not known to be Django: let the upstream tracer try
        if not routes:
            return None
        return self._describe_django(db_path, routes, route_path, depth)

    def _build_django_routes(self, db_path: Path) -> list:
        from . import graph_queries as gq
        from .django_routes import DjangoRoutes

        with gq.connect(db_path) as conn:
            nodes = {
                (path, line): {"id": nid, "qualified_name": qn or name, "path": path, "line": line}
                for nid, name, qn, line, path in conn.execute(
                    """
                    SELECT n.id, n.name, n.qualified_name, n.start_line, f.path
                    FROM nodes n JOIN files f ON n.file_id = f.id
                    WHERE f.path LIKE '%.py' AND n.type IN ('function', 'method', 'class')
                    """
                )
            }
        return DjangoRoutes(self._py_indexer, nodes).build()

    def _describe_django(self, db_path: Path, routes: list, route_path: str, depth: int) -> Dict[str, Any]:
        from . import graph_queries as gq
        from .django_routes import match_routes, split_route

        try:
            with gq.connect(db_path) as conn:

                def describe(route, with_calls):
                    entry = {
                        "path": "/" + route.path,
                        "handlers": [{k: v for k, v in h.items() if k != "node_id"} for h in route.handlers],
                        "urls_file": route.urls_file,
                    }
                    if route.loose:
                        entry["note"] = "URL matched only with converter types ignored (e.g. a non-uuid id)"
                    if with_calls:
                        ids = [h["node_id"] for h in route.handlers if "node_id" in h]
                        entry["downstream"] = gq.downstream_calls(conn, ids, depth=depth)
                    return entry

                base = {"framework": "django", "route": route_path, "route_count": len(routes)}
                if not route_path.strip():
                    # One line per route: '/path  GET handler, POST handler'.
                    def line(route):
                        handlers = ", ".join(
                            f"{h.get('method', 'ANY')} {h.get('handler', '?')}" for h in route.handlers
                        )
                        return f"/{route.path}  {handlers}" + ("  (loose match)" if route.loose else "")

                    listed = [line(r) for r in routes[:300]]
                    return dict(
                        base,
                        routes=listed,
                        routes_truncated=len(routes) > len(listed),
                        hint="Pass one URL or pattern ('GET /api/x/1/') to get its handlers' downstream calls.",
                    )
                matches = match_routes(routes, route_path)
                if not matches:
                    # Last path word that is not an id: 'GET /api/users/12/' -> 'users'
                    words = [
                        w for w in split_route(route_path)[1].lower().split("/")
                        if w and not any(ch.isdigit() for ch in w)
                    ]
                    near = [r for r in routes if words and words[-1] in r.path.lower()]
                    return dict(
                        base,
                        matches=[],
                        hint="No route matched. Pass the URL ('GET /api/x/1/') or a pattern from candidates.",
                        candidates=["/" + r.path for r in near[:20]],
                    )
                return dict(base, matches=[describe(r, True) for r in matches[:5]])
        except Exception as e:
            return {"framework": "django", "route": route_path, "error": str(e)}

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

        result = self._mcp_then_cli(
            "get_call_graph",
            {
                "symbol_name": symbol,
                "direction": direction,
                "depth": depth,
                # Newer servers default to min_confidence="inferred", hiding
                # ambiguous by-name edges; include them for full blast radius.
                "min_confidence": "ambiguous",
            },
            cli_args,
            cli_parsers.parse_callgraph,
        )
        # Current servers answer {"function", "callers", "callees"}; give it
        # the documented {"symbol", "nodes": [{name, location, kind}]} shape too.
        if isinstance(result, dict) and "function" in result and "nodes" not in result:
            result["symbol"] = result["function"]
            result["nodes"] = [
                node for key in ("callers", "callees") for node in self._call_graph_nodes(result.get(key), key)
            ]
        return result

    @staticmethod
    def _call_graph_nodes(entries: Any, direction: str) -> List[Dict[str, Any]]:
        """Call-graph entries as {name, location, kind, depth, direction} nodes.

        A dense graph comes back as file-level rollups ({"rollups": [{file,
        names, node_ids, min_depth}]}) instead of a node list.
        """
        if isinstance(entries, dict):
            return [
                {
                    "name": name,
                    "node_id": node_id,
                    "location": {"file_path": r.get("file"), "start_line": None},
                    "kind": None,
                    "depth": r.get("min_depth"),
                    "direction": direction,
                }
                for r in entries.get("rollups") or []
                if isinstance(r, dict)
                for name, node_id in zip(r.get("names") or [], r.get("node_ids") or [])
            ]
        return [
            {
                "name": n.get("name"),
                "location": {"file_path": n.get("file_path"), "start_line": n.get("start_line")},
                "kind": n.get("type"),
                "depth": n.get("depth"),
                "direction": n.get("direction") or direction,
            }
            for n in entries or []
            if isinstance(n, dict)
        ]

    def get_ast_node(
        self, symbol: str, include_source: bool = True, file_path: str = None
    ) -> Dict[str, Any]:
        """Get detailed info about a symbol.

        Functions, methods and classes are read straight from the SQLite
        index (accepts 'file.py:name'); other symbols go to upstream.
        """
        node = self.lookup_symbol(f"{file_path}:{symbol}" if file_path else symbol)
        if node:
            node["source"] = node["code_content"]
            return node
        return self.upstream_ast_node(symbol, include_source, file_path)

    def upstream_ast_node(
        self, symbol: str, include_source: bool = True, file_path: str = None
    ) -> Dict[str, Any]:
        """Ask code-graph itself (any symbol kind, not only functions/classes)."""
        cli_args = ["show", symbol]
        if file_path:
            cli_args.extend(["--file", file_path])
        if not include_source:
            cli_args.append("--compact")

        result = self._mcp_then_cli(
            "get_ast_node",
            {"symbol_name": symbol},
            cli_args,
            cli_parsers.parse_show,
        )
        # Newer servers return the source as "code_content" instead of "source"
        if (
            isinstance(result, dict)
            and "source" not in result
            and "code_content" in result
        ):
            result["source"] = result["code_content"]
        return result

    def lookup_symbol(self, spec: str) -> Optional[Dict[str, Any]]:
        """Best function/method/class matching `spec` from the SQLite index.

        Includes code_content and other_definitions (the remaining matches).
        None when there is no index or no match.
        """
        from . import graph_queries as gq

        db_path = self.code_graph_path / "index.db"
        if not db_path.exists():
            return None
        try:
            with gq.connect(db_path) as conn:
                matches = gq.find_symbol(conn, spec, gq.SYMBOL_TYPES)
                if not matches:
                    return None
                node = dict(matches[0])
                node["start_line"] = node.pop("line")
                node["code_content"] = gq.code_content(conn, node["id"]) or ""
                node["other_definitions"] = [gq.describe(m) for m in matches[1:10]]
                if node["type"] in gq.FUNC_TYPES:
                    node["callees"] = gq.callees_of(conn, node["id"])
                    found = gq.callers_of(conn, node["id"])
                    node["callers"] = found["callers"]
                    node["test_caller_count"] = len(found["test_callers"])
                    node["ambiguous_caller_count"] = len(found["ambiguous"])
                return node
        except Exception:
            return None

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
        # Note: the CLI --include-tests flag was removed upstream; the MCP
        # find_references tool defaults include_tests to true.

        def _parser(stdout: str) -> List[Dict[str, Any]]:
            return cli_parsers.parse_refs(stdout)

        result = self._mcp_then_cli(
            "find_references",
            {"symbol_name": symbol, "include_tests": include_tests},
            cli_args,
            _parser,
        )
        if isinstance(result, list):
            return result
        # Current servers answer {"references": [...]}: return the list, in
        # the CLI parser's {relation, name, location} shape plus extra fields.
        # (_mcp_then_cli wraps a bare list as {"results": [...]}.)
        refs = (
            result.get("references", result.get("results")) if isinstance(result, dict) else None
        )
        if not isinstance(refs, list):
            return []
        return [
            {
                **r,
                "location": {"file_path": r.get("file_path"), "start_line": r.get("start_line")},
            }
            for r in refs
            if isinstance(r, dict)
        ]

    def project_map(self) -> Dict[str, Any]:
        """Modules, module dependencies, entry points and hot functions.

        Reads the CLI's `map --json`: the MCP `project_map` tool caps its
        arrays to fit its token budget (15 of 71 modules and of 209
        dependencies on a large project) and says so only in
        `_array_truncations`. The MCP tool is the fallback.
        """
        result = self._run_cli("map", "--json", timeout=120)
        self._raise_if_building(result)
        if result.get("success"):
            try:
                data = json.loads(result["stdout"])
                if isinstance(data, dict) and "modules" in data:
                    return data
            except ValueError:
                pass
        return self._mcp_then_cli("project_map", {}, ["map"], cli_parsers.parse_map)

    def module_overview(self, module_path: str) -> Dict[str, Any]:
        """Get overview of a specific module."""
        result = self._mcp_then_cli(
            "module_overview",
            {"path": module_path},
            ["overview", module_path],
            cli_parsers.parse_overview,
        )
        # Current servers list "active_exports"; add the documented per-file
        # {"files": {path: [{kind, names}]}} view the CLI parser returns.
        if isinstance(result, dict) and "files" not in result and "active_exports" in result:
            files: Dict[str, Dict[str, List[str]]] = {}
            for item in result.get("active_exports") or []:
                kinds = files.setdefault(item.get("file", ""), {})
                kinds.setdefault(item.get("type", "symbol"), []).append(item.get("name"))
            result["files"] = {
                path: [{"kind": kind, "names": names} for kind, names in kinds.items()]
                for path, kinds in files.items()
            }
        return result

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

    def tour(self, path: str = None) -> Dict[str, Any]:
        """Dependency-ordered reading tour for the project or a subtree.

        Uses the CLI `tour` command (the Rust MCP server does not expose it as
        a tool). Lists modules from foundational to entry-point so reading
        top-to-bottom orients you from the ground up.

        Args:
            path: Optional subtree to scope the tour to (None = whole project)

        Returns:
            Dict with a "reading_order" list of module entries, each with
            path, role, depended_on_by, depends_on, key_symbols, in_cycle
        """
        args = ["tour"]
        if path:
            args.append(path)
        args.append("--json")
        return self._cli_parse(cli_parsers.parse_tour, *args)

    def central_functions(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Rank functions by betweenness centrality over the call graph.

        CLI-only upstream. Surfaces structural chokepoints that lie on the most
        shortest call paths between other functions — complementing degree-based
        hot functions (a chokepoint can have few callers yet route most
        cross-cluster traffic).
        """
        return self._cli_parse(
            cli_parsers.parse_json_array,
            "centrality",
            "--limit",
            str(limit),
            "--json",
        )

    def import_cycles(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Detect circular import dependencies (file-level strongly-connected sets).

        CLI-only upstream. Each entry: files, size, and a representative shortest
        loop. An empty list means no circular imports — the healthy case.
        """
        return self._cli_parse(
            cli_parsers.parse_json_array,
            "cycles",
            "--limit",
            str(limit),
            "--json",
        )

    def surprising_connections(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Surface unexpected cross-module couplings (uncertain/sole-bridge edges).

        CLI-only upstream. Ranks calls/references edges by resolution confidence
        (ambiguous > inferred > extracted), module-boundary crossing, and
        sole-bridge status — good audit input before refactors.
        """
        return self._cli_parse(
            cli_parsers.parse_json_array,
            "surprising",
            "--limit",
            str(limit),
            "--json",
        )

    def project_health(self, limit: int = 15) -> Dict[str, Any]:
        """Aggregate architecture-health snapshot.

        Combines betweenness chokepoints, circular imports, and surprising
        cross-module couplings into one dict for the trellis_project_health
        tool / UI health view.
        """
        return {
            "chokepoints": self.central_functions(limit),
            "import_cycles": self.import_cycles(),
            "surprising_connections": self.surprising_connections(limit),
        }

    # CLI commands that write the index and need the server's lock. Every
    # other command opens the index read-only and runs beside the server.
    _CLI_WRITERS = {"rebuild-index", "incremental-index", "reindex", "index"}

    def _run_cli(self, *args, timeout: int = 300) -> Dict[str, Any]:
        """Run a code-graph-mcp CLI command.

        Index writers stop the server first (it holds the index lock) and
        keep it stopped until they finish; readers leave it running, so a
        CLI fallback no longer restarts the server and its file watcher.

        Returns a dict with stdout, stderr, returncode and success; a timeout
        or missing binary is reported as a failure, not raised.
        """
        import subprocess
        import time

        cmd = [str(self.binary_path)] + list(args)

        def run():
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=os.environ.copy(),
                cwd=str(self.project_path),
                timeout=timeout,
            )

        try:
            if args and args[0] in self._CLI_WRITERS:
                with self._lock:
                    self._close_locked()
                    time.sleep(0.5)  # let the OS release the index lock
                    result = run()
            else:
                result = run()
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"stdout": "", "stderr": str(e), "returncode": -1, "success": False}

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }

    def sync_project(self) -> Dict[str, Any]:
        """Full rebuild of the project index.

        Runs 'rebuild-index --confirm' and then re-applies Trellis's Python
        call-edge and JS dynamic-dispatch augmentations. This is the heavy
        path — needed once to index a project, and afterwards only after
        large external changes (huge git pulls, branch switches) or when the
        index looks stale. Day-to-day edits are picked up automatically by
        the server-side file watcher (armed by the bridge on every spawn).
        """
        # Close any running MCP server to avoid DB lock conflicts
        self.close()

        result = self._run_cli("rebuild-index", "--confirm")

        if result["success"]:
            with self._edge_lock:
                self._apply_custom_edges(result)
        return result

    def _apply_custom_edges(self, result: Dict[str, Any]) -> None:
        """After a rebuild: Python call edges, JS augmentation, edge-state record."""
        import traceback

        try:
            from .python_call_indexer import PythonCallGraphIndexer

            self._py_indexer = PythonCallGraphIndexer(str(self.project_path))
            result["call_indexer"] = self._py_indexer.index_calls()
        except Exception as e:
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
            result["js_augmentor_error"] = str(e)
            result["js_augmentor_traceback"] = traceback.format_exc()

        # Custom edges now match this index: record it so the next query
        # does not re-link everything again.
        try:
            self._edge_snapshot = self._index_snapshot(self.code_graph_path / "index.db")
            self._save_edge_state(self._edge_snapshot)
            self._edge_fingerprint = self._fingerprint(self.code_graph_path / "index.db")
        except Exception:
            self._edge_snapshot = None

    def incremental_sync(self) -> Dict[str, Any]:
        """Re-index files changed since the last index, then re-link custom edges.

        Runs the CLI `incremental-index` (hash-based: unchanged files are
        skipped), so it also catches edits made while no server was watching.
        """
        result = self._run_cli("incremental-index")
        if result["success"]:
            db_path = self.code_graph_path / "index.db"
            with self._edge_lock:
                self._relink_if_changed(db_path)
            result["status"] = self.health_check()
        return result

    # ------------------------------------------------------------------
    # Visualizer API (converts to our format)
    # ------------------------------------------------------------------

    def get_graph_for_visualizer(self, max_nodes: int = 2000) -> Dict[str, Any]:
        """Get graph data formatted for the visualizer.

        Returns all functions while the project has at most max_nodes
        functions; larger projects get a simplified view (modules plus one
        representative function per file) so consumers are never handed an
        unbounded graph dump.
        """
        total = self._count_functions()
        if total > max_nodes:
            return self._get_simplified_graph({"nodes_count": total})
        return self._get_full_graph()

    def _count_functions(self) -> int:
        """Count indexed functions/methods (0 when the DB is unavailable)."""
        import sqlite3

        db_path = self.code_graph_path / "index.db"
        if not db_path.exists():
            return 0

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM nodes
                WHERE type IN ('function', 'method')
                  AND name NOT LIKE 'test_%'
                  AND name != '__init__'
                  AND name != '<module>'
            """)
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

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
            symbol_count = sum(
                mod.get(k, 0) or 0
                for k in ("functions", "classes", "interfaces_traits", "constants", "other")
            ) or mod.get("symbols", 0)
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
        """Impact analysis as a graph for the visualizer.

        Built from get_feature_impact, like the impact tools: the root links
        to its callers (and, with depth > 1, indirect callers) and to the
        other functions in its file.

        Returns:
            {"nodes", "links", "stats", "root_function", "risk_level", "view_mode"}
        """
        report = self.get_feature_impact(symbol, depth=depth)
        risk = report.get("risk_level", "unknown")
        root = report.get("resolved") or symbol
        nodes: List[Dict[str, Any]] = [
            {"id": f"func:{root}", "type": "function", "label": symbol, "name": symbol,
             "is_root": True, "risk_level": risk}
        ]
        links: List[Dict[str, Any]] = []
        groups = (
            ("caller", report.get("callers", [])),
            ("indirect_caller", report.get("indirect_callers", [])),
            ("same_file", report.get("same_file_functions", [])),
        )
        seen = {nodes[0]["id"]}
        for impact_type, entries in groups:
            for func in entries:
                node_id = f"func:{func['file_path']}:{func['qualified_name']}"
                if node_id in seen:
                    continue
                seen.add(node_id)
                nodes.append(
                    {
                        "id": node_id,
                        "type": "function",
                        "label": func.get("name") or func["qualified_name"],
                        "name": func.get("name") or func["qualified_name"],
                        "qualified_name": func["qualified_name"],
                        "file_path": func["file_path"],
                        "line": func.get("line"),
                        "impact_type": impact_type,
                    }
                )
                links.append({"source": node_id, "target": nodes[0]["id"], "type": impact_type})

        return {
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_nodes": len(nodes),
                "total_links": len(links),
                "risk_level": risk,
                "affected_count": len(nodes) - 1,
            },
            "root_function": root,
            "risk_level": risk,
            "view_mode": "impact",
            **({"error": report["error"]} if report.get("error") else {}),
        }

    # ------------------------------------------------------------------
    # Feature Impact Analysis
    # ------------------------------------------------------------------

    def get_feature_impact(
        self, symbol: str, depth: int = 1, refresh: bool = True
    ) -> Dict[str, Any]:
        """Callers, same-file functions, feature impacts and pointers for a symbol.

        Refreshes the index first (unless refresh=False, for callers that
        already did) so edits made moments ago are reflected.
        """
        if refresh:
            self.ensure_fresh()

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
        """Every function/method in the files a project.md feature covers."""
        from .feature_impact import ProjectContextParser

        context = ProjectContextParser(str(self.project_path))
        if feature_name not in context.features:
            return []
        return self.functions_in_files(
            context.files_for_feature(feature_name, self.indexed_paths())
        )

    def indexed_paths(self) -> List[str]:
        """Every file path in the code-graph index."""
        from . import graph_queries as gq

        import sqlite3

        db_path = self.code_graph_path / "index.db"
        if not db_path.exists():
            return []
        try:
            with gq.connect(db_path) as conn:
                return [row[0] for row in conn.execute("SELECT path FROM files")]
        except sqlite3.Error:
            return []

    def functions_in_files(self, paths: List[str]) -> List[Dict[str, Any]]:
        """Functions/methods defined in the given indexed files."""
        from . import graph_queries as gq

        import sqlite3

        db_path = self.code_graph_path / "index.db"
        if not paths or not db_path.exists():
            return []
        try:
            with gq.connect(db_path) as conn:
                conn.execute("CREATE TEMP TABLE wanted (path TEXT PRIMARY KEY)")
                conn.executemany("INSERT OR IGNORE INTO wanted VALUES (?)", ((p,) for p in paths))
                rows = conn.execute(
                    """
                    SELECT n.id, n.name, n.qualified_name, f.path,
                           n.start_line, n.end_line, n.type
                    FROM wanted w
                    JOIN files f ON f.path = w.path
                    JOIN nodes n ON n.file_id = f.id
                    WHERE n.type IN ('function', 'method')
                    ORDER BY f.path, n.start_line
                    """
                ).fetchall()
        except sqlite3.Error:
            return []

        return [
            {
                "node_id": r[0],
                "name": r[1],
                "qualified_name": r[2] or r[1],
                "file_path": r[3],
                "start_line": r[4],
                "end_line": r[5],
                "type": r[6],
            }
            for r in rows
        ]

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
            try:
                note = note_graph.get_note(candidate)
            except ValueError:
                continue  # invalid id (e.g. spaces in a multi-word feature name)
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

        self._ensure_call_edges()
        functions = self.get_feature_functions(feature_name)
        hot_functions = self._hot_functions(functions)

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

    def _hot_functions(self, functions: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
        """Most-called non-test functions, counting confident non-test callers only."""
        from . import graph_queries as gq
        from .utils import is_test_path

        db_path = self.code_graph_path / "index.db"
        candidates = [f for f in functions if not is_test_path(f["file_path"])]
        if not candidates or not db_path.exists():
            return []
        try:
            with gq.connect(db_path) as conn:
                counts = gq.caller_counts(conn, [f["node_id"] for f in candidates])
        except Exception:
            return []
        ranked = sorted(
            (dict(f, caller_count=counts[f["node_id"]]) for f in candidates if counts.get(f["node_id"])),
            key=lambda f: -f["caller_count"],
        )
        return ranked[:limit]

    def find_feature_for_function(self, function_path: str) -> Optional[str]:
        """Find which project.md feature owns a source file path."""
        from .feature_impact import ProjectContextParser

        context = ProjectContextParser(str(self.project_path))
        feature = context.get_feature_for_file(function_path)
        return feature.feature_name if feature else None
