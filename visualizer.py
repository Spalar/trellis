from __future__ import annotations

from typing import Any, Dict, List

from store import GraphStore


class GraphVisualizer:
    """Serialize project graph into D3.js-friendly nodes/links payload."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def export_graph(self, project_id: str) -> Dict[str, Any]:
        features = self.store.list_features(project_id)
        functions = self.store.list_functions(project_id)

        nodes: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        node_ids = set()

        palette = [
            "#ff6b6b", "#4ecdc4", "#45b7d1", "#96ceb4",
            "#ffeaa7", "#dfe6e9", "#fd79a8", "#a29bfe",
            "#00b894", "#e17055", "#74b9ff", "#fab1a0",
        ]
        feature_color = {}
        for i, f in enumerate(sorted(features, key=lambda x: x.feature_name)):
            feature_color[f.feature_name] = palette[i % len(palette)]

        # Feature nodes
        for f in features:
            node_id = f"feature:{f.feature_name}"
            node_ids.add(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "name": f.feature_name,
                    "type": "feature",
                    "function_count": len(f.functions),
                    "dependencies": f.dependencies,
                    "color": feature_color.get(f.feature_name, "#888"),
                }
            )
            for dep in f.dependencies:
                links.append(
                    {
                        "source": node_id,
                        "target": f"feature:{dep}",
                        "type": "feature_dep",
                    }
                )

        # Function nodes
        for fn in functions:
            node_id = f"func:{fn.function_path}"
            node_ids.add(node_id)
            short_name = fn.function_path.split(".")[-1]
            nodes.append(
                {
                    "id": node_id,
                    "name": short_name,
                    "full_name": fn.function_path,
                    "type": "function",
                    "feature": fn.feature_name,
                    "feature_color": feature_color.get(fn.feature_name, "#888"),
                    "file_path": fn.file_path,
                    "line": fn.start_line,
                    "docstring": (fn.docstring or "")[:300],
                }
            )
            # Function belongs to feature
            links.append(
                {
                    "source": node_id,
                    "target": f"feature:{fn.feature_name}",
                    "type": "belongs_to",
                }
            )

        # Function call links
        for fn in functions:
            source_id = f"func:{fn.function_path}"
            for callee in fn.callees:
                target_id = f"func:{callee}"
                if target_id in node_ids:
                    links.append(
                        {
                            "source": source_id,
                            "target": target_id,
                            "type": "calls",
                        }
                    )

        return {
            "project_id": project_id,
            "nodes": nodes,
            "links": links,
            "stats": {
                "total_features": len(features),
                "total_functions": len(functions),
                "total_links": len(links),
            },
        }

    def export_impact_subgraph(
        self, project_id: str, function_path: str, impact_report: dict = None
    ) -> Dict[str, Any]:
        """Export only the portion of the graph reachable from callers of a function."""
        graph = self.export_graph(project_id)

        target_id = f"func:{function_path}"
        reachable = {target_id}
        queue = [target_id]

        # Build reverse adjacency (caller -> callee is link.source->link.target for calls)
        # For impact we want callers upstream: we need to traverse call links in reverse
        call_links = [
            link for link in graph["links"] if link["type"] == "calls"
        ]
        callee_to_callers: Dict[str, List[str]] = {}
        for link in call_links:
            callee_to_callers.setdefault(link["target"], []).append(link["source"])

        while queue:
            current = queue.pop(0)
            for caller in callee_to_callers.get(current, []):
                if caller not in reachable:
                    reachable.add(caller)
                    queue.append(caller)

        # Also include the feature nodes for all reachable functions
        feature_nodes = set()
        for n in graph["nodes"]:
            if n["id"] in reachable:
                feature_nodes.add(f"feature:{n.get('feature', '')}")

        all_reachable = reachable | feature_nodes

        filtered_nodes = [n for n in graph["nodes"] if n["id"] in all_reachable]
        filtered_links = [
            link
            for link in graph["links"]
            if link["source"] in all_reachable and link["target"] in all_reachable
        ]

        # Enrich nodes with risk levels from impact report
        if impact_report:
            risk_map = {}
            for group in impact_report.get("risk_groups", []):
                for func_path in group.get("functions", []):
                    risk_map[f"func:{func_path}"] = group["risk"]

            for node in filtered_nodes:
                if node["id"] in risk_map:
                    node["risk_level"] = risk_map[node["id"]]

        return {
            "project_id": project_id,
            "root_function": function_path,
            "nodes": filtered_nodes,
            "links": filtered_links,
            "stats": {
                "total_nodes": len(filtered_nodes),
                "total_links": len(filtered_links),
            },
        }
