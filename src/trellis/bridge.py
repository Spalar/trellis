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
from typing import Any, Dict, List, Optional


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
            
            env = os.environ.copy()
            env["CODE_GRAPH_PROJECT"] = str(self.project_path)
            
            self._proc = subprocess.Popen(
                [str(self.binary_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=1,  # Line buffered
            )
    
    def _call(self, tool_name: str, **arguments) -> Dict[str, Any]:
        """Call an MCP tool via JSON-RPC.
        
        Args:
            tool_name: Name of the tool (e.g., "impact_analysis")
            **arguments: Tool arguments
            
        Returns:
            Tool result as dict
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
            
            # Read response
            response_line = self._proc.stdout.readline()
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
                        return {"text": text}
            
            return result
    
    def close(self) -> None:
        """Clean up subprocess."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                self._proc.kill()
            self._proc = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
    
    # ------------------------------------------------------------------
    # High-level API
    # ------------------------------------------------------------------
    
    def analyze_impact(self, symbol: str, depth: int = 3) -> Dict[str, Any]:
        """Analyze impact of changing a symbol.
        
        Args:
            symbol: Function/symbol name to analyze
            depth: Call graph traversal depth
            
        Returns:
            Impact report with risk_level, affected_functions, etc.
        """
        return self._call("impact_analysis", symbol=symbol, depth=depth)
    
    def search(self, query: str, language: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Semantic code search.
        
        Args:
            query: Search query
            language: Filter by language (optional)
            limit: Max results
            
        Returns:
            List of matching symbols
        """
        args = {"query": query, "limit": limit}
        if language:
            args["language"] = language
        
        result = self._call("semantic_code_search", **args)
        return result.get("results", [])
    
    def get_call_graph(
        self,
        symbol: str,
        direction: str = "both",
        depth: int = 2,
    ) -> Dict[str, Any]:
        """Get call graph for a symbol.
        
        Args:
            symbol: Function/symbol name
            direction: "callers", "callees", or "both"
            depth: Traversal depth
            
        Returns:
            Call graph structure
        """
        return self._call(
            "get_call_graph",
            symbol=symbol,
            direction=direction,
            depth=depth,
        )
    
    def get_ast_node(self, symbol: str, include_source: bool = True) -> Dict[str, Any]:
        """Get detailed info about a symbol.
        
        Args:
            symbol: Symbol name
            include_source: Include source code in result
            
        Returns:
            AST node details
        """
        return self._call(
            "get_ast_node",
            symbol=symbol,
            include_source=include_source,
        )
    
    def find_references(self, symbol: str, include_tests: bool = True) -> List[Dict[str, Any]]:
        """Find all references to a symbol.
        
        Args:
            symbol: Symbol to find references for
            include_tests: Include test references
            
        Returns:
            List of references
        """
        result = self._call(
            "find_references",
            symbol=symbol,
            include_tests=include_tests,
        )
        return result.get("references", [])
    
    def project_map(self) -> Dict[str, Any]:
        """Get full project architecture overview.
        
        Returns:
            Project structure with modules, entry points, hot functions
        """
        return self._call("project_map")
    
    def module_overview(self, module_path: str) -> Dict[str, Any]:
        """Get overview of a specific module.
        
        Args:
            module_path: Path to module (e.g., "src/auth")
            
        Returns:
            Module structure with exports and symbols
        """
        return self._call("module_overview", module_path=module_path)
    
    def trace_http_route(self, route: str) -> Dict[str, Any]:
        """Trace HTTP route to handler and downstream calls.
        
        Args:
            route: Route path (e.g., "/api/users")
            
        Returns:
            Request flow from route to data layer
        """
        return self._call("trace_http_chain", route=route)
    
    def find_dead_code(self, path: str = None) -> List[Dict[str, Any]]:
        """Find unused code.
        
        Args:
            path: Limit search to path (optional)
            
        Returns:
            List of unused symbols
        """
        args = {}
        if path:
            args["path"] = path
        
        result = self._call("find_dead_code", **args)
        return result.get("dead_code", [])
    
    def dependency_graph(self, file_path: str) -> Dict[str, Any]:
        """Get dependency graph for a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Dependencies and dependents
        """
        return self._call("dependency_graph", file=file_path)
    
    def health_check(self) -> Dict[str, Any]:
        """Check index status and health.
        
        Returns:
            Health status with node counts, freshness
        """
        return self._call("get_index_status")
