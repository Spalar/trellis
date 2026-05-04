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

# Avoid circular import - FeatureImpactAnalyzer imported inside methods


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
        result = self._call("impact_analysis", symbol_name=symbol, depth=depth)
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
            symbol_name=symbol,
            direction=direction,
            depth=depth,
        )
        return self._normalize_result(result)
    
    def get_ast_node(self, symbol: str, include_source: bool = True) -> Dict[str, Any]:
        """Get detailed info about a symbol."""
        result = self._call(
            "get_ast_node",
            symbol_name=symbol,
            include_source=include_source,
        )
        return self._normalize_result(result)
    
    def find_references(self, symbol: str, include_tests: bool = True) -> List[Dict[str, Any]]:
        """Find all references to a symbol."""
        result = self._call(
            "find_references",
            symbol_name=symbol,
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
            nodes.append({
                "id": f"mod:{mod_path}",
                "type": "module",
                "label": mod_path,
                "name": mod_path,
            })
        
        # Query SQLite directly for all functions (bypasses MCP token limits)
        db_path = self.project_path / ".code-graph" / "index.db"
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
            except Exception as e:
                # Fall back to MCP tool if DB query fails
                search_result = self._call("ast_search", type="fn", limit=100)
                if isinstance(search_result, dict):
                    all_funcs = search_result.get("results", [])
                elif isinstance(search_result, list):
                    all_funcs = search_result
        
        for func in all_funcs:
            func_id = func.get("qualified_name") or func.get("name", "unknown")
            file_path = func.get("file_path", "")
            
            nodes.append({
                "id": f"func:{func_id}",
                "type": "function",
                "label": func.get("name", func_id),
                "name": func.get("name", ""),
                "file_path": file_path,
                "line": func.get("start_line", 0),
                "kind": func.get("type", "function"),
                "signature": func.get("signature", ""),
            })
            
            # Link to module
            if file_path:
                if "/" not in file_path:
                    links.append({
                        "source": f"func:{func_id}",
                        "target": "mod:<root>",
                        "type": "belongs_to"
                    })
                else:
                    best_mod = None
                    best_len = 0
                    for mod in modules:
                        mod_path = mod.get("path", "")
                        if mod_path and mod_path != "<root>" and file_path.startswith(mod_path + "/"):
                            if len(mod_path) > best_len:
                                best_len = len(mod_path)
                                best_mod = mod_path
                    if best_mod:
                        links.append({
                            "source": f"func:{func_id}",
                            "target": f"mod:{best_mod}",
                            "type": "belongs_to"
                        })
        
        return {
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_nodes": len(nodes),
                "total_functions": len([n for n in nodes if n["type"] == "function"]),
                "total_modules": len([n for n in nodes if n["type"] == "module"]),
                "total_links": len(links),
            },
            "view_mode": "full"
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
            nodes.append({
                "id": f"mod:{mod_path}",
                "type": "module",
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
        
        # Query SQLite for representative functions from different files
        db_path = self.project_path / ".code-graph" / "index.db"
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
                
            nodes.append({
                "id": f"func:{func_id}",
                "type": "function",
                "label": func_name,
                "name": func_name,
                "file_path": func.get("file_path", ""),
                "signature": func.get("signature", ""),
                "caller_count": func.get("caller_count", 0),
                "is_hot": True,
            })
            
            # Link to module
            file_path = func.get("file_path", "")
            if file_path:
                if "/" not in file_path:
                    links.append({
                        "source": f"func:{func_id}",
                        "target": "mod:<root>",
                        "type": "belongs_to"
                    })
                else:
                    best_mod = None
                    best_len = 0
                    for mod in modules:
                        mod_path = mod.get("path", "")
                        if mod_path and mod_path != "<root>" and file_path.startswith(mod_path + "/"):
                            if len(mod_path) > best_len:
                                best_len = len(mod_path)
                                best_mod = mod_path
                    if best_mod:
                        links.append({
                            "source": f"func:{func_id}",
                            "target": f"mod:{best_mod}",
                            "type": "belongs_to"
                        })
        
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
        
        Args:
            symbol: Function/symbol to analyze
            depth: Call graph depth
            
        Returns:
            Impact report with affected functions, features, divergence, pointers
        """
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
