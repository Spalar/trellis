"""Trellis MCP server - graph-based code analysis and impact detection.

This module exposes both HTTP routes (for the visualizer) and MCP tools
(for AI coding agents). Uses code-graph-mcp via CodeGraphBridge.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

import time
from typing import Dict, List, Optional

from analytics import AnalyticsStore
from auth import validate_auth
from spec_manager import SpecManager

# Load environment variables from .env file
load_dotenv(Path(__file__).with_name(".env"))

# Ensure banner is suppressed via env var
if os.environ.get("FASTMCP_SHOW_SERVER_BANNER") is None:
    os.environ["FASTMCP_SHOW_SERVER_BANNER"] = "false"
if os.environ.get("FASTMCP_LOG_LEVEL") is None:
    os.environ["FASTMCP_LOG_LEVEL"] = "ERROR"

VERSION = "0.2.0"

mcp = FastMCP(
    "trellis-core",
    version=VERSION,
    strict_input_validation=True,
    mask_error_details=True,
)

# ------------------------------------------------------------------
# State (initialized once at import time)
# ------------------------------------------------------------------
_spec_manager = SpecManager()
_analytics = AnalyticsStore()

# Cache bridge instances by project path
_bridge_cache: Dict[str, "CodeGraphBridge"] = {}


def _resolve_project_path(project_id: str) -> str:
    """Resolve project ID to actual path.
    
    Searches multiple locations and prefers paths with a .git directory.
    Never resolves to a directory inside the trellis repo to avoid polluting it.
    """
    if project_id == "trellis":
        return str(Path(__file__).parent)
    
    path = Path(project_id)
    trellis_root = Path(__file__).parent.resolve()
    
    if path.is_absolute() and path.exists():
        resolved = path.resolve()
        # Don't allow resolving to inside trellis unless it's trellis itself
        if not _is_inside_trellis(resolved, trellis_root):
            return str(resolved)
    
    # Collect all candidate paths (excluding trellis internals)
    candidates = []
    
    # 1. Relative to current directory
    if path.exists():
        resolved = path.resolve()
        if not _is_inside_trellis(resolved, trellis_root):
            candidates.append(resolved)
    
    # 2. Sibling of trellis root (common pattern: repos/ProjectName)
    sibling = trellis_root.parent / project_id
    if sibling.exists() and sibling.resolve() not in candidates:
        candidates.append(sibling.resolve())
    
    # 3. Parent of current directory
    parent_sibling = Path.cwd().parent / project_id
    if parent_sibling.exists() and parent_sibling.resolve() not in candidates:
        candidates.append(parent_sibling.resolve())
    
    # 4. Check if user provided absolute path that doesn't exist yet
    if path.is_absolute():
        return str(path)
    
    if not candidates:
        return project_id  # Fallback
    
    # Prefer the candidate with a .git directory
    for candidate in candidates:
        if (candidate / ".git").exists():
            return str(candidate)
    
    # Otherwise return the first candidate
    return str(candidates[0])


def _is_inside_trellis(path: Path, trellis_root: Path) -> bool:
    """Check if a path is inside the trellis repository directory."""
    try:
        path.relative_to(trellis_root)
        return True
    except ValueError:
        return False


def _get_bridge(project_id: str) -> "CodeGraphBridge":
    """Get or create bridge for project."""
    if project_id not in _bridge_cache:
        from src.trellis import CodeGraphBridge
        resolved = _resolve_project_path(project_id)
        _bridge_cache[project_id] = CodeGraphBridge(resolved)
    return _bridge_cache[project_id]


def _track_tool(tool_name: str):
    """Decorator to track tool calls with timing."""
    def decorator(func):
        import functools
        @functools.wraps(func)
        async def wrapper(project_id: str = "", **kwargs):
            start = time.perf_counter()
            status = "success"
            error_msg = None
            
            try:
                result = await func(project_id=project_id, **kwargs)
                return result
            except Exception as e:
                status = "error"
                error_msg = str(e)
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                _analytics.record_tool_call(
                    tool_name=tool_name,
                    duration_ms=duration_ms,
                    status=status,
                    project_id=project_id,
                    error_message=error_msg,
                )
        
        return wrapper
    return decorator


# ------------------------------------------------------------------
# MCP Tools
# ------------------------------------------------------------------

@mcp.tool()
@_track_tool("trellis_sync")
async def trellis_sync(
    project_id: str = "",
    repo_path: str = "",
    config_path: str = ".trellis/config.yaml",
    incremental: bool = False,
) -> str:
    """Sync a project repository into the graph.
    
    Uses code-graph-mcp to index the codebase.
    """
    try:
        resolved = _resolve_project_path(repo_path or project_id)
        
        # Clear bridge cache for this project to avoid DB lock conflicts
        cache_key = repo_path or project_id
        if cache_key in _bridge_cache:
            _bridge_cache[cache_key].close()
            del _bridge_cache[cache_key]
        
        bridge = _get_bridge(cache_key)
        
        # Trigger actual indexing
        if incremental:
            sync_result = bridge.incremental_sync()
        else:
            sync_result = bridge.sync_project()
        
        # After sync, we need a fresh bridge since the old one closed its process
        if cache_key in _bridge_cache:
            _bridge_cache[cache_key].close()
            del _bridge_cache[cache_key]
        
        # Get fresh bridge for health check
        bridge = _get_bridge(cache_key)
        health = bridge.health_check()
        
        return json.dumps({
            "status": "ok",
            "project_id": project_id,
            "nodes": health.get("nodes_count", 0),
            "files": health.get("files_count", 0),
            "message": "Project synced successfully",
            "sync_details": sync_result,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)
    

@mcp.tool()
@_track_tool("trellis_get_feature")
async def trellis_get_feature(
    project_id: str = "",
    feature_name: str = "",
) -> str:
    """Get feature context and functions."""
    try:
        bridge = _get_bridge(project_id)
        module = bridge.module_overview(feature_name)
        return json.dumps(module, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_analyze_impact")
async def trellis_analyze_impact(
    project_id: str = "",
    function_path: str = "",
    depth_mode: str = "standard",
) -> str:
    """Analyze impact of changing a function.
    
    Returns both technical and feature-level impact.
    """
    try:
        bridge = _get_bridge(project_id)
        
        # Get feature impact report
        report = bridge.get_feature_impact(function_path, depth=3)
        
        # Format for MCP
        result = {
            "symbol": function_path,
            "risk_level": report.get("risk_level", "unknown"),
            "affected_functions": report.get("affected_functions_count", 0),
            "affected_files": report.get("affected_files_count", 0),
            "feature_impacts": [
                {
                    "feature": fi["feature_name"],
                    "functions": len(fi["impacted_functions"]),
                    "decisions": [d["id"] for d in fi["affected_decisions"]],
                    "risks": fi["risk_flags"],
                }
                for fi in report.get("feature_impacts", [])
            ],
            "development_pointers": report.get("development_pointers", [])[:10],  # Limit
            "divergence_warnings": report.get("divergence_warnings", []),
        }
        
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_get_function")
async def trellis_get_function(
    project_id: str = "",
    function_path: str = "",
) -> str:
    """Get details for a specific function.
    
    function_path can be:
    - Just the function name: "create_llm"
    - Qualified name: "AIFactory.create_llm" 
    - File:function format: "backend/ai/factory.py:create_llm"
    """
    try:
        bridge = _get_bridge(project_id)
        
        # Handle file:function format by extracting just the function name
        symbol = function_path
        if ":" in function_path:
            symbol = function_path.split(":")[-1]
        
        # Try direct lookup first
        node = bridge.get_ast_node(symbol)
        
        # If not found, try searching by name in SQLite
        if not node or (isinstance(node, dict) and ("error" in node or not node.get("name"))):
            import sqlite3
            from src.trellis.utils import resolve_code_graph_db
            
            db_path = resolve_code_graph_db(bridge.project_path)
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                
                # Search by name or qualified_name
                cursor.execute("""
                    SELECT n.name, n.qualified_name, f.path, n.start_line, n.end_line, n.type, n.signature, n.code_content
                    FROM nodes n
                    JOIN files f ON n.file_id = f.id
                    WHERE (n.name = ? OR n.qualified_name = ?)
                      AND n.type IN ('function', 'method')
                    LIMIT 5
                """, (symbol, symbol))
                
                rows = cursor.fetchall()
                conn.close()
                
                if rows:
                    # Return the first match
                    row = rows[0]
                    node = {
                        "name": row[0],
                        "qualified_name": row[1] or row[0],
                        "file_path": row[2],
                        "start_line": row[3],
                        "end_line": row[4],
                        "type": row[5],
                        "signature": row[6] or "",
                        "code_content": row[7] or "",
                    }
                else:
                    node = {"error": f"Function '{symbol}' not found in index"}
        
        return json.dumps(node, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_search")
async def trellis_search(
    project_id: str = "",
    query: str = "",
    limit: int = 10,
) -> str:
    """Search for functions by concept."""
    try:
        bridge = _get_bridge(project_id)
        results = bridge.search(query, limit=limit)
        return json.dumps({"results": results}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_list_features")
async def trellis_list_features(
    project_id: str = "",
) -> str:
    """List all features/modules in the project."""
    try:
        bridge = _get_bridge(project_id)
        pmap = bridge.project_map()
        modules = pmap.get("modules", [])
        return json.dumps({
            "features": [
                {
                    "name": m.get("path", "unknown"),
                    "symbol_count": m.get("symbol_count", 0),
                }
                for m in modules
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_trace_path")
async def trellis_trace_path(
    project_id: str = "",
    from_feature: str = "",
    to_feature: str = "",
) -> str:
    """Trace dependency path between two features/modules.
    
    Features can be module paths like "backend/ai/providers" or "backend/chat".
    """
    try:
        bridge = _get_bridge(project_id)
        
        # Use SQLite to find connections between modules
        import sqlite3
        from src.trellis.utils import resolve_code_graph_db
        
        db_path = resolve_code_graph_db(bridge.project_path)
        if not db_path.exists():
            return json.dumps({"error": "No code-graph database found. Run trellis_sync first."}, indent=2)
        
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Find files in from_feature module
        cursor.execute("""
            SELECT DISTINCT f.path 
            FROM files f 
            WHERE f.path LIKE ?
        """, (f"%{from_feature}%",))
        from_files = [row[0] for row in cursor.fetchall()]
        
        # Find files in to_feature module
        cursor.execute("""
            SELECT DISTINCT f.path 
            FROM files f 
            WHERE f.path LIKE ?
        """, (f"%{to_feature}%",))
        to_files = [row[0] for row in cursor.fetchall()]
        
        if not from_files:
            return json.dumps({"error": f"Module '{from_feature}' not found"}, indent=2)
        if not to_files:
            return json.dumps({"error": f"Module '{to_feature}' not found"}, indent=2)
        
        # Find import/call edges between the modules
        cursor.execute("""
            SELECT DISTINCT 
                sf.path as from_file,
                tf.path as to_file,
                s.name as symbol_name,
                e.relation
            FROM edges e
            JOIN nodes s ON e.source_id = s.id
            JOIN nodes t ON e.target_id = t.id
            JOIN files sf ON s.file_id = sf.id
            JOIN files tf ON t.file_id = tf.id
            WHERE e.relation IN ('calls', 'imports')
              AND sf.path LIKE ?
              AND tf.path LIKE ?
            LIMIT 20
        """, (f"%{from_feature}%", f"%{to_feature}%"))
        
        direct_connections = []
        for row in cursor.fetchall():
            direct_connections.append({
                "from_file": row[0],
                "to_file": row[1],
                "symbol": row[2],
                "relation": row[3]
            })
        
        conn.close()
        
        return json.dumps({
            "from_feature": from_feature,
            "to_feature": to_feature,
            "from_files_count": len(from_files),
            "to_files_count": len(to_files),
            "direct_connections": direct_connections,
            "connection_count": len(direct_connections),
            "from_files_sample": from_files[:5],
            "to_files_sample": to_files[:5],
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_visualize_graph")
async def trellis_visualize_graph(
    project_id: str = "",
) -> str:
    """Get graph data for visualization."""
    try:
        bridge = _get_bridge(project_id)
        graph = bridge.get_graph_for_visualizer(max_nodes=200)
        return json.dumps(graph, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_detect_hotspots")
async def trellis_detect_hotspots(
    project_id: str = "",
) -> str:
    """Find high-centrality functions (potential hotspots)."""
    try:
        bridge = _get_bridge(project_id)
        pmap = bridge.project_map()
        hot = pmap.get("hot_functions", [])[:20]
        return json.dumps({"hotspots": hot}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_analyze_diff")
async def trellis_analyze_diff(
    project_id: str = "",
    diff: str = "",
    compare_branch: str = "",
) -> str:
    """Analyze impact of a code diff.
    
    Automatically detects changed functions from git diff and runs impact analysis.
    
    Args:
        project_id: Project to analyze
        diff: Optional raw diff string. If not provided, fetches from git.
        compare_branch: Branch to compare against (default: origin/main or main)
    """
    try:
        import re
        import sqlite3
        from src.trellis.utils import resolve_code_graph_db
        
        resolved = _resolve_project_path(project_id)
        
        # Resolve symlinks/junctions to real path for git operations
        resolved_real = str(Path(resolved).resolve())
        
        # Step 1: Get diff if not provided
        if not diff:
            try:
                import git
                repo = git.Repo(resolved_real)
                
                # Determine what to diff against
                if compare_branch:
                    target = compare_branch
                else:
                    # Try common default branches
                    for branch in ["origin/main", "origin/master", "main", "master"]:
                        try:
                            repo.rev_parse(branch)
                            target = branch
                            break
                        except git.BadName:
                            continue
                    else:
                        # No remote branch, get unstaged changes
                        diff = repo.git.diff()
                        target = None
                
                if target and not diff:
                    diff = repo.git.diff(target)
                
                if not diff:
                    return json.dumps({
                        "status": "no_changes",
                        "message": "No changes detected in git working tree",
                        "project": project_id,
                    }, indent=2)
                    
            except ImportError:
                return json.dumps({
                    "error": "GitPython not installed. Either install it (pip install GitPython) or provide the diff parameter directly: trellis_analyze_diff(project_id='...', diff='...')",
                }, indent=2)
            except git.InvalidGitRepositoryError:
                return json.dumps({
                    "error": f"'{resolved_real}' is not a git repository.\n\nTo fix:\n1. Pass the absolute path: trellis_analyze_diff(project_id='/path/to/repo')\n2. Or provide the diff directly: trellis_analyze_diff(project_id='{project_id}', diff='...')",
                }, indent=2)
        
        # Step 2: Parse unified diff to find changed files and line numbers
        changed_files = {}  # file_path -> set of changed line numbers
        current_file = None
        current_line = 0
        
        for line in diff.split('\n'):
            # File header: --- a/path or +++ b/path
            if line.startswith('--- ') or line.startswith('+++ '):
                # Extract file path, skip /dev/null
                path = line[4:].split('\t')[0]
                if path.startswith('a/'):
                    path = path[2:]
                elif path.startswith('b/'):
                    path = path[2:]
                if path != '/dev/null':
                    current_file = path
                    if current_file not in changed_files:
                        changed_files[current_file] = set()
            
            # Hunk header: @@ -old_start,old_len +new_start,new_len @@
            elif line.startswith('@@') and current_file:
                match = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
                if match:
                    current_line = int(match.group(1))
            
            # Added line (in new file)
            elif line.startswith('+') and not line.startswith('+++') and current_file:
                changed_files[current_file].add(current_line)
                current_line += 1
            
            # Removed line (skip in new file line counting)
            elif line.startswith('-') and not line.startswith('---') and current_file:
                # Line was removed, note it but don't increment counter
                pass
            
            # Context line
            elif current_file and not line.startswith('\\'):
                current_line += 1
        
        # Filter out files with no actual line changes
        changed_files = {f: lines for f, lines in changed_files.items() if lines}
        
        if not changed_files:
            return json.dumps({
                "status": "no_changes",
                "message": "No file changes detected in diff",
                "diff_length": len(diff),
            }, indent=2)
        
        # Step 3: Query SQLite to find affected functions
        db_path = resolve_code_graph_db(resolved)
        affected_functions = []
        
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                
                for file_path, line_numbers in changed_files.items():
                    if not line_numbers:
                        continue
                    
                    # Find functions in this file that overlap with changed lines
                    cursor.execute("""
                        SELECT DISTINCT n.name, n.qualified_name, f.path, 
                               n.start_line, n.end_line, n.type
                        FROM nodes n
                        JOIN files f ON n.file_id = f.id
                        WHERE f.path LIKE ?
                          AND n.type IN ('function', 'method', 'class')
                          AND n.start_line <= ?
                          AND n.end_line >= ?
                    """, (f'%{file_path}%', max(line_numbers), min(line_numbers)))
                    
                    for row in cursor.fetchall():
                        # Verify actual overlap with changed lines
                        func_start, func_end = row[3], row[4]
                        changed_in_func = [line for line in line_numbers if func_start <= line <= func_end]
                        
                        if changed_in_func:
                            affected_functions.append({
                                "name": row[0],
                                "qualified_name": row[1] or row[0],
                                "file_path": row[2],
                                "type": row[5],
                                "changed_lines": len(changed_in_func),
                                "line_range": [func_start, func_end],
                            })
                
                conn.close()
            except Exception:
                # Continue without function-level details
                pass
        
        # Step 4: Run impact analysis on unique affected functions
        bridge = _get_bridge(project_id)
        impact_results = []
        analyzed_symbols = set()
        
        for func in affected_functions:
            symbol = func["qualified_name"] or func["name"]
            if symbol in analyzed_symbols:
                continue
            analyzed_symbols.add(symbol)
            
            try:
                report = bridge.get_feature_impact(symbol, depth=2)
                impact_results.append({
                    "symbol": symbol,
                    "file_path": func["file_path"],
                    "type": func["type"],
                    "changed_lines": func["changed_lines"],
                    "risk_level": report.get("risk_level", "unknown"),
                    "affected_functions": report.get("affected_functions_count", 0),
                    "affected_files": report.get("affected_files_count", 0),
                    "feature_impacts": [
                        {
                            "feature": fi["feature_name"],
                            "functions": len(fi["impacted_functions"]),
                        }
                        for fi in report.get("feature_impacts", [])
                    ],
                })
            except Exception as e:
                impact_results.append({
                    "symbol": symbol,
                    "file_path": func["file_path"],
                    "error": str(e),
                })
        
        # Step 5: Calculate overall risk
        risk_scores = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}
        max_risk = max(
            [risk_scores.get(r["risk_level"], 0) for r in impact_results if "risk_level" in r],
            default=0
        )
        risk_labels = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: "unknown"}
        overall_risk = risk_labels.get(max_risk, "unknown")
        
        total_affected_funcs = sum(
            r.get("affected_functions", 0) 
            for r in impact_results 
            if "affected_functions" in r
        )
        
        return json.dumps({
            "status": "ok",
            "project": project_id,
            "overall_risk": overall_risk,
            "changed_files_count": len(changed_files),
            "changed_files": list(changed_files.keys()),
            "affected_functions_count": len(affected_functions),
            "unique_functions_analyzed": len(impact_results),
            "total_downstream_affected": total_affected_funcs,
            "functions": impact_results,
        }, indent=2)
        
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_get_boundary_map")
async def trellis_get_boundary_map(
    project_id: str = "",
) -> str:
    """Get module boundary map."""
    try:
        bridge = _get_bridge(project_id)
        pmap = bridge.project_map()
        modules = pmap.get("modules", [])
        deps = pmap.get("module_dependencies", [])
        return json.dumps({
            "modules": [m.get("path") for m in modules],
            "dependencies": deps,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


# ------------------------------------------------------------------
# Knowledge Graph Tools
# ------------------------------------------------------------------

@mcp.tool()
@_track_tool("trellis_create_note")
async def trellis_create_note(
    project_id: str = "",
    note_id: str = "",
    title: str = "",
    content: str = "",
    tags: str = "",
) -> str:
    """Create or update a knowledge note.
    
    Notes support markdown with [[links]] and @mentions.
    """
    try:
        from src.trellis.knowledge_graph import NoteGraph
        
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        
        tag_list = [t.strip() for t in tags.split(",")] if tags else []
        note = graph.save_note(note_id, content, title=title, tags=tag_list)
        
        return json.dumps({
            "status": "ok",
            "note_id": note.id,
            "title": note.title,
            "links": note.links,
            "mentions": note.mentions,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_get_note")
async def trellis_get_note(
    project_id: str = "",
    note_id: str = "",
) -> str:
    """Get a knowledge note by ID."""
    try:
        from src.trellis.knowledge_graph import NoteGraph
        
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        note = graph.get_note(note_id)
        
        if not note:
            return json.dumps({"error": f"Note '{note_id}' not found"}, indent=2)
        
        return json.dumps({
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "tags": note.tags,
            "links": note.links,
            "mentions": note.mentions,
            "backlinks": graph.get_backlinks(note_id),
            "created": note.created_at,
            "updated": note.updated_at,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_search_notes")
async def trellis_search_notes(
    project_id: str = "",
    query: str = "",
) -> str:
    """Search knowledge notes by content."""
    try:
        from src.trellis.knowledge_graph import NoteGraph
        
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        results = graph.search_notes(query)
        
        return json.dumps({
            "query": query,
            "results": [
                {
                    "id": n.id,
                    "title": n.title,
                    "excerpt": n.content[:200] + "..." if len(n.content) > 200 else n.content,
                    "tags": n.tags,
                }
                for n in results
            ]
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_delete_note")
async def trellis_delete_note(
    project_id: str = "",
    note_id: str = "",
) -> str:
    """Delete a knowledge note."""
    try:
        from src.trellis.knowledge_graph import NoteGraph
        
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        success = graph.delete_note(note_id)
        
        return json.dumps({
            "status": "ok" if success else "error",
            "message": f"Note '{note_id}' deleted" if success else f"Note '{note_id}' not found",
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
@_track_tool("trellis_knowledge_graph")
async def trellis_knowledge_graph(
    project_id: str = "",
) -> str:
    """Get the full knowledge graph (notes + code nodes + edges)."""
    try:
        from src.trellis.knowledge_graph import NoteGraph
        
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        data = graph.build_graph(include_code=True)
        
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


# ------------------------------------------------------------------
# HTTP Routes
# ------------------------------------------------------------------

@mcp.custom_route("/", methods=["GET"])
async def root(request: Request):
    """Serve visualizer HTML."""
    visualizer_path = Path(__file__).parent / "visualizer.html"
    if visualizer_path.exists():
        return FileResponse(visualizer_path)
    return JSONResponse({"message": "Trellis MCP Server", "version": VERSION})


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    """Health check."""
    return JSONResponse({"status": "ok", "version": VERSION})


@mcp.custom_route("/graph/{project_id}", methods=["GET"])
async def graph_get(request: Request):
    """Get graph data."""
    project_id = request.path_params["project_id"]
    try:
        bridge = _get_bridge(project_id)
        graph = bridge.get_graph_for_visualizer()
        return JSONResponse(graph)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/graph/{project_id}/impact/{symbol}", methods=["GET"])
async def graph_impact(request: Request):
    """Get impact graph."""
    project_id = request.path_params["project_id"]
    symbol = request.path_params["symbol"]
    try:
        bridge = _get_bridge(project_id)
        graph = bridge.get_impact_graph(symbol)
        return JSONResponse(graph)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/feature/{project_id}/impact/{symbol}", methods=["GET"])
async def feature_impact(request: Request):
    """Get feature impact report."""
    project_id = request.path_params["project_id"]
    symbol = request.path_params["symbol"]
    try:
        bridge = _get_bridge(project_id)
        report = bridge.get_feature_impact(symbol)
        return JSONResponse(report)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/feature/{project_id}/pointers/{symbol}", methods=["GET"])
async def feature_pointers(request: Request):
    """Get development pointers."""
    project_id = request.path_params["project_id"]
    symbol = request.path_params["symbol"]
    try:
        bridge = _get_bridge(project_id)
        pointers = bridge.get_development_pointers(symbol)
        return JSONResponse({"pointers": pointers})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/spec/{project_id}", methods=["GET", "POST"])
async def spec_handler(request: Request):
    """Handle project spec."""
    project_id = request.path_params["project_id"]
    
    if request.method == "GET":
        spec = _spec_manager.load_spec(project_id)
        if spec is None:
            template = _spec_manager.create_template(project_id)
            return JSONResponse({
                "project_id": project_id,
                "status": "no_spec",
                "content": template,
            })
        return JSONResponse({
            "project_id": project_id,
            "status": "ok",
            "content": spec.content,
        })
    
    else:  # POST
        body = await request.json()
        content = body.get("content", "")
        _spec_manager.save_spec(project_id, content)
        return JSONResponse({"project_id": project_id, "status": "ok"})


@mcp.custom_route("/analytics", methods=["GET"])
async def analytics_dashboard(request: Request):
    """Serve analytics dashboard."""
    dashboard_path = Path(__file__).parent / "analytics.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return JSONResponse({"error": "Analytics dashboard not found"}, status_code=404)


@mcp.custom_route("/knowledge-graph/{project_id}", methods=["GET"])
async def knowledge_graph_get(request: Request):
    """Get knowledge graph data (notes + code)."""
    project_id = request.path_params["project_id"]
    try:
        from src.trellis.knowledge_graph import NoteGraph
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        data = graph.build_graph(include_code=True)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/note/{project_id}/{note_id}", methods=["GET", "POST", "DELETE"])
async def note_handler(request: Request):
    """Get, save, or delete a knowledge note."""
    project_id = request.path_params["project_id"]
    note_id = request.path_params["note_id"]
    
    try:
        from src.trellis.knowledge_graph import NoteGraph
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        
        if request.method == "GET":
            note = graph.get_note(note_id)
            if not note:
                return JSONResponse({"error": f"Note '{note_id}' not found"}, status_code=404)
            return JSONResponse({
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "tags": note.tags,
                "links": note.links,
                "mentions": note.mentions,
                "backlinks": graph.get_backlinks(note_id),
            })
        elif request.method == "POST":
            body = await request.json()
            content = body.get("content", "")
            title = body.get("title", note_id)
            tags = body.get("tags", [])
            note = graph.save_note(note_id, content, title=title, tags=tags)
            return JSONResponse({"status": "ok", "note_id": note.id, "title": note.title})
        elif request.method == "DELETE":
            success = graph.delete_note(note_id)
            if not success:
                return JSONResponse({"error": f"Note '{note_id}' not found"}, status_code=404)
            return JSONResponse({"status": "ok", "message": f"Note '{note_id}' deleted"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/notes/{project_id}", methods=["GET"])
async def notes_list(request: Request):
    """List all knowledge notes."""
    project_id = request.path_params["project_id"]
    try:
        from src.trellis.knowledge_graph import NoteGraph
        resolved = _resolve_project_path(project_id)
        graph = NoteGraph(resolved)
        notes = [
            {
                "id": n.id,
                "title": n.title,
                "tags": n.tags,
                "updated": n.updated_at,
            }
            for n in graph.notes.values()
        ]
        return JSONResponse({"notes": notes, "count": len(notes)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Determine transport from environment
    transport = os.environ.get("TRELLIS_TRANSPORT", "stdio")
    
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # HTTP mode - Use FastAPI for REST endpoints
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse
        import uvicorn
        
        http_app = FastAPI(title="Trellis API", version=VERSION)
        
        # Enable CORS
        http_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        @http_app.get("/")
        async def root():
            """Serve visualizer HTML."""
            visualizer_path = Path(__file__).parent / "visualizer.html"
            if visualizer_path.exists():
                return FileResponse(visualizer_path)
            return {"message": "Trellis MCP Server", "version": VERSION}
        
        @http_app.get("/health")
        async def health():
            return {"status": "ok", "version": VERSION}
        
        @http_app.get("/graph/{project_id}")
        async def graph_get(project_id: str):
            try:
                bridge = _get_bridge(project_id)
                graph = bridge.get_graph_for_visualizer(max_nodes=200)
                return graph
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/graph/{project_id}/impact/{symbol}")
        async def graph_impact(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                graph = bridge.get_impact_graph(symbol)
                return graph
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/feature/{project_id}/impact/{symbol}")
        async def feature_impact(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                report = bridge.get_feature_impact(symbol)
                return report
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/feature/{project_id}/context/{symbol}")
        async def feature_context(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                context = bridge.get_feature_context(symbol)
                if context:
                    return context
                raise HTTPException(status_code=404, detail="No feature context found")
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/feature/{project_id}/pointers/{symbol}")
        async def feature_pointers(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                pointers = bridge.get_development_pointers(symbol)
                return {"pointers": pointers}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/feature/{project_id}/divergence/{symbol}")
        async def feature_divergence(project_id: str, symbol: str):
            try:
                bridge = _get_bridge(project_id)
                warnings = bridge.check_feature_divergence(symbol)
                return {"symbol": symbol, "divergence_warnings": warnings, "has_divergence": len(warnings) > 0}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/spec/{project_id}")
        async def spec_get(project_id: str):
            spec = _spec_manager.load_spec(project_id)
            if spec is None:
                template = _spec_manager.create_template(project_id)
                return {"project_id": project_id, "status": "no_spec", "content": template}
            return {"project_id": project_id, "status": "ok", "content": spec.content}
        
        @http_app.post("/spec/{project_id}")
        async def spec_post(project_id: str, content: str = ""):
            _spec_manager.save_spec(project_id, content)
            return {"project_id": project_id, "status": "ok"}
        
        @http_app.get("/analytics")
        async def analytics_dashboard():
            dashboard_path = Path(__file__).parent / "analytics.html"
            if dashboard_path.exists():
                return FileResponse(dashboard_path)
            raise HTTPException(status_code=404, detail="Dashboard not found")
        
        @http_app.get("/knowledge-graph/{project_id}")
        async def knowledge_graph_get(project_id: str):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                data = graph.build_graph(include_code=True)
                return data
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/note/{project_id}/{note_id}")
        async def note_get(project_id: str, note_id: str):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                note = graph.get_note(note_id)
                if not note:
                    raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found")
                return {
                    "id": note.id,
                    "title": note.title,
                    "content": note.content,
                    "tags": note.tags,
                    "links": note.links,
                    "mentions": note.mentions,
                    "backlinks": graph.get_backlinks(note_id),
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.post("/note/{project_id}/{note_id}")
        async def note_post(project_id: str, note_id: str, body: dict):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                content = body.get("content", "")
                title = body.get("title", note_id)
                tags = body.get("tags", [])
                note = graph.save_note(note_id, content, title=title, tags=tags)
                return {"status": "ok", "note_id": note.id, "title": note.title}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.delete("/note/{project_id}/{note_id}")
        async def note_delete(project_id: str, note_id: str):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                success = graph.delete_note(note_id)
                if not success:
                    raise HTTPException(status_code=404, detail=f"Note '{note_id}' not found")
                return {"status": "ok", "message": f"Note '{note_id}' deleted"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/notes/{project_id}")
        async def notes_list(project_id: str):
            try:
                from src.trellis.knowledge_graph import NoteGraph
                resolved = _resolve_project_path(project_id)
                graph = NoteGraph(resolved)
                notes = [
                    {
                        "id": n.id,
                        "title": n.title,
                        "tags": n.tags,
                        "updated": n.updated_at,
                    }
                    for n in graph.notes.values()
                ]
                return {"notes": notes, "count": len(notes)}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @http_app.get("/projects")
        async def list_projects():
            """List synced projects with graph data."""
            from src.trellis.utils import get_trellis_data_dir
            projects = []
            
            # Only show projects that have been synced (have .code-graph data)
            trellis_data = get_trellis_data_dir()
            projects_dir = trellis_data / "projects"
            if projects_dir.exists():
                for item in projects_dir.iterdir():
                    if item.is_dir():
                        code_graph_dir = item / ".code-graph"
                        # Only include if .code-graph exists and has index.db
                        if code_graph_dir.exists() and (code_graph_dir / "index.db").exists():
                            projects.append({
                                "id": item.name,
                                "name": item.name,
                                "path": str(item),
                            })
            
            return {"projects": projects}
        
        uvicorn.run(http_app, host="0.0.0.0", port=17317)
