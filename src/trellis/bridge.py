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
    
    def _call(self, tool_name: str, **arguments) -> Union[Dict, List, str]:
        """Call an MCP tool via JSON-RPC.
        
        Args:
            tool_name: Name of the tool (e.g., "impact_analysis")
            **arguments: Tool arguments
            
        Returns:
            Tool result (dict, list, or string)
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
                        return text
            
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
    
    def _normalize_result(self, result: Any) -> Dict[str, Any]:
        """Normalize MCP result to dict format."""
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"results": result}
        if isinstance(result, str):
            return {"text": result}
        return {}
    
    def analyze_impact(self, symbol: str, depth: int = 3) -> Dict[str, Any]:
        """Analyze impact of changing a symbol."""
        result = self._call("impact_analysis", symbol=symbol, depth=depth)
        return self._normalize_result(result)
    
    def search(self, query: str, language: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Semantic code search."""
        args = {"query": query, "limit": limit}
        if language:
            args["language"] = language
        
        result = self._call("semantic_code_search", **args)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("results", [])
        return []
    
    def get_call_graph(
        self,
        symbol: str,
        direction: str = "both",
        depth: int = 2,
    ) -> Dict[str, Any]:
        """Get call graph for a symbol."""
        result = self._call(
            "get_call_graph",
            symbol=symbol,
            direction=direction,
            depth=depth,
        )
        return self._normalize_result(result)
    
    def get_ast_node(self, symbol: str, include_source: bool = True) -> Dict[str, Any]:
        """Get detailed info about a symbol."""
        result = self._call(
            "get_ast_node",
            symbol=symbol,
            include_source=include_source,
        )
        return self._normalize_result(result)
    
    def find_references(self, symbol: str, include_tests: bool = True) -> List[Dict[str, Any]]:
        """Find all references to a symbol."""
        result = self._call(
            "find_references",
            symbol=symbol,
            include_tests=include_tests,
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("references", [])
        return []
    
    def project_map(self) -> Dict[str, Any]:
        """Get full project architecture overview."""
        result = self._call("project_map")
        return self._normalize_result(result)
    
    def module_overview(self, module_path: str) -> Dict[str, Any]:
        """Get overview of a specific module."""
        result = self._call("module_overview", module_path=module_path)
        return self._normalize_result(result)
    
    def trace_http_route(self, route: str) -> Dict[str, Any]:
        """Trace HTTP route to handler and downstream calls."""
        result = self._call("trace_http_chain", route=route)
        return self._normalize_result(result)
    
    def find_dead_code(self, path: str = None) -> List[Dict[str, Any]]:
        """Find unused code."""
        args = {}
        if path:
            args["path"] = path
        
        result = self._call("find_dead_code", **args)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("dead_code", [])
        return []
    
    def dependency_graph(self, file_path: str) -> Dict[str, Any]:
        """Get dependency graph for a file."""
        result = self._call("dependency_graph", file=file_path)
        return self._normalize_result(result)
    
    def health_check(self) -> Dict[str, Any]:
        """Check index status and health."""
        result = self._call("get_index_status")
        return self._normalize_result(result)
    
    # ------------------------------------------------------------------
    # Visualizer API (converts to our format)
    # ------------------------------------------------------------------
    
    def get_graph_for_visualizer(self, max_nodes: int = 200) -> Dict[str, Any]:
        """Get graph data formatted for the visualizer.
        
        Returns nodes and links in our visualizer format.
        Uses simplified view if >200 functions.
        
        Returns:
            {
                "nodes": [{"id": "...", "type": "feature|function", ...}],
                "links": [{"source": "...", "target": "..."}],
                "stats": {...},
                "view_mode": "full|simplified"
            }
        """
        health = self.health_check()
        total_nodes = health.get("nodes_count", 0)
        
        if total_nodes > max_nodes:
            return self._get_simplified_graph(health)
        else:
            return self._get_full_graph()
    
    def _get_full_graph(self) -> Dict[str, Any]:
        """Get full detailed graph for small repos."""
        # Get project map for structure
        pmap = self.project_map()
        
        nodes = []
        links = []
        
        # Add module nodes as features
        modules = pmap.get("modules", [])
        for mod in modules:
            mod_path = mod.get("path", "")
            nodes.append({
                "id": f"mod:{mod_path}",
                "type": "feature",
                "label": mod_path,
                "name": mod_path,
            })
        
        # Search for all functions
        all_funcs = self.search("*", limit=1000)
        
        for func in all_funcs:
            func_id = func.get("qualified_name", func.get("name", "unknown"))
            nodes.append({
                "id": f"func:{func_id}",
                "type": "function",
                "label": func.get("name", func_id),
                "name": func.get("name", ""),
                "file_path": func.get("file_path", ""),
                "line": func.get("line", 0),
                "kind": func.get("kind", "function"),
            })
            
            # Link to module
            file_path = func.get("file_path", "")
            if file_path:
                # Find matching module
                for mod in modules:
                    mod_path = mod.get("path", "")
                    if mod_path and file_path.startswith(mod_path):
                        links.append({
                            "source": f"func:{func_id}",
                            "target": f"mod:{mod_path}",
                            "type": "belongs_to"
                        })
                        break
        
        return {
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_nodes": len(nodes),
                "total_functions": len([n for n in nodes if n["type"] == "function"]),
                "total_features": len([n for n in nodes if n["type"] == "feature"]),
                "total_links": len(links),
            },
            "view_mode": "full"
        }
    
    def _get_simplified_graph(self, health: Dict) -> Dict[str, Any]:
        """Get simplified graph for large repos (>200 functions).
        
        Shows only modules and their relationships.
        """
        pmap = self.project_map()
        
        nodes = []
        links = []
        
        # Add module nodes
        modules = pmap.get("modules", [])
        for mod in modules:
            mod_path = mod.get("path", "")
            symbol_count = mod.get("symbol_count", 0)
            nodes.append({
                "id": f"mod:{mod_path}",
                "type": "feature",
                "label": mod_path,
                "name": mod_path,
                "symbol_count": symbol_count,
                "is_module": True,
            })
        
        # Add module dependency links
        deps = pmap.get("module_dependencies", [])
        for dep in deps:
            from_mod = dep.get("from", "")
            to_mod = dep.get("to", "")
            if from_mod and to_mod:
                links.append({
                    "source": f"mod:{from_mod}",
                    "target": f"mod:{to_mod}",
                    "type": "depends_on"
                })
        
        # Add top hot functions as representative samples
        hot_funcs = pmap.get("hot_functions", [])[:20]  # Limit to top 20
        for func in hot_funcs:
            func_name = func.get("name", "")
            func_id = func.get("qualified_name", func_name)
            if not func_id:
                continue
                
            nodes.append({
                "id": f"func:{func_id}",
                "type": "function",
                "label": func_name,
                "name": func_name,
                "caller_count": func.get("caller_count", 0),
                "is_hot": True,
            })
            
            # Link to module
            file_path = func.get("file_path", "")
            for mod in modules:
                mod_path = mod.get("path", "")
                if mod_path and file_path.startswith(mod_path):
                    links.append({
                        "source": f"func:{func_id}",
                        "target": f"mod:{mod_path}",
                        "type": "belongs_to"
                    })
                    break
        
        return {
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_nodes": health.get("nodes_count", 0),
                "total_functions": health.get("nodes_count", 0),  # Approximate
                "total_features": len(modules),
                "total_links": len(links),
                "shown_nodes": len(nodes),
                "shown_functions": len([n for n in nodes if n["type"] == "function"]),
                "note": f"Simplified view: showing {len(nodes)} of {health.get('nodes_count', 0)} nodes",
            },
            "view_mode": "simplified"
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
        nodes.append({
            "id": root_id,
            "type": "function",
            "label": symbol,
            "name": symbol,
            "is_root": True,
            "risk_level": impact.get("risk_level", "unknown"),
        })
        
        # Add affected functions
        affected = impact.get("affected_functions", [])
        for func in affected:
            func_name = func.get("name", func.get("qualified_name", "unknown"))
            func_id = f"func:{func_name}"
            
            nodes.append({
                "id": func_id,
                "type": "function",
                "label": func_name,
                "name": func_name,
                "file_path": func.get("file_path", ""),
                "confidence": func.get("confidence", 0),
                "impact_type": func.get("impact_type", "affected"),
            })
            
            links.append({
                "source": root_id,
                "target": func_id,
                "type": "impacts",
                "confidence": func.get("confidence", 0),
            })
        
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
            "view_mode": "impact"
        }
