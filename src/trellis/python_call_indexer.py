"""Python Call Graph Indexer for code-graph-mcp.

Populates the .code-graph/index.db with 'calls' edges by parsing
Python source files using the standard ast module.

This makes code-graph-mcp's impact_analysis and get_call_graph tools
work correctly for Python codebases.
"""

import ast
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .utils import resolve_code_graph_db


class PythonCallGraphIndexer:
    """Extracts call relationships from Python files and inserts into code-graph DB."""

    def __init__(self, project_path: str) -> None:
        self.project_path = Path(project_path)
        self.db_path = resolve_code_graph_db(project_path)

    def index_calls(self) -> Dict[str, int]:
        """Index all Python call relationships.

        Returns:
            Stats dict with counts
        """
        if not self.db_path.exists():
            return {"error": "No code-graph database found"}

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # Get all Python function/method nodes from the database
        cursor.execute("""
            SELECT n.id, n.name, n.qualified_name, f.path, n.start_line, n.end_line, n.type
            FROM nodes n
            JOIN files f ON n.file_id = f.id
            WHERE f.path LIKE '%.py'
              AND n.type IN ('function', 'method')
              AND n.name NOT LIKE 'test_%'
              AND n.name != '__init__'
              AND n.name != '<module>'
        """)

        # Build lookup tables
        funcs_by_file: Dict[str, List[Tuple[int, str, str, int, int]]] = {}
        # (node_id, name, qualified_name, start_line, end_line)

        for row in cursor.fetchall():
            node_id, name, qname, file_path, start_line, end_line, node_type = row
            if file_path not in funcs_by_file:
                funcs_by_file[file_path] = []
            funcs_by_file[file_path].append(
                (node_id, name, qname or name, start_line, end_line)
            )

        # Extract call edges
        edges_added = 0

        for file_path, functions in funcs_by_file.items():
            full_path = self.project_path / file_path
            if not full_path.exists():
                continue

            try:
                source = full_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError):
                continue

            # For each function in this file, find calls within it
            for node_id, func_name, qualified_name, start_line, end_line in functions:
                # Find the AST node for this function
                func_ast = self._find_function_at_line(tree, start_line)
                if not func_ast:
                    continue

                # Extract all calls within this function
                calls = self._extract_calls(func_ast)

                # Resolve each call to a target function
                for call_name in calls:
                    target_id = self._resolve_call(
                        cursor, call_name, file_path, functions
                    )
                    if target_id and target_id != node_id:
                        # Insert call edge
                        try:
                            cursor.execute(
                                """
                                INSERT OR IGNORE INTO edges (source_id, target_id, relation, metadata)
                                VALUES (?, ?, 'calls', ?)
                            """,
                                (
                                    node_id,
                                    target_id,
                                    f'{{"caller": "{qualified_name}"}}',
                                ),
                            )
                            edges_added += 1
                        except sqlite3.IntegrityError:
                            pass

        conn.commit()
        conn.close()

        return {
            "files_processed": len(funcs_by_file),
            "functions_found": sum(len(f) for f in funcs_by_file.values()),
            "call_edges_added": edges_added,
        }

    def _find_function_at_line(self, tree: ast.AST, line: int) -> Optional[ast.AST]:
        """Find function/method definition at given line number."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.lineno == line:
                    return node
        return None

    def _extract_calls(self, func_node: ast.AST) -> Set[str]:
        """Extract all call names from a function body."""
        calls = set()

        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                call_name = self._get_call_name(node.func)
                if call_name:
                    calls.add(call_name)

        return calls

    def _get_call_name(self, node: ast.AST) -> Optional[str]:
        """Get the name of a called function from the AST."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            # For method calls like self.foo() or obj.bar(),
            # extract the method name
            return node.attr
        return None

    def _resolve_call(
        self,
        cursor: sqlite3.Cursor,
        call_name: str,
        file_path: str,
        local_functions: List[Tuple[int, str, str, int, int]],
    ) -> Optional[int]:
        """Resolve a call name to a target function node_id."""
        # 1. Check local functions first (same file)
        for node_id, func_name, qname, _, _ in local_functions:
            if func_name == call_name or qname == call_name:
                return node_id

        # 2. Check imported functions
        cursor.execute(
            """
            SELECT n.id
            FROM nodes n
            JOIN files f ON n.file_id = f.id
            WHERE n.name = ?
              AND n.type IN ('function', 'method')
            LIMIT 1
        """,
            (call_name,),
        )

        row = cursor.fetchone()
        if row:
            return row[0]

        return None


if __name__ == "__main__":
    import sys
    import json

    project = sys.argv[1] if len(sys.argv) > 1 else "."
    indexer = PythonCallGraphIndexer(project)
    result = indexer.index_calls()
    print(json.dumps(result, indent=2))
