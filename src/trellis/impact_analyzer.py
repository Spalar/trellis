"""Impact analysis for Trellis.

Resolves the symbol to one indexed definition, finds its callers by node id
(so same-named functions elsewhere are not mixed in), then maps the root and
its callers onto project.md features for decisions, risks and pointers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from . import graph_queries as gq
from .bridge import CodeGraphBridge
from .feature_impact import (
    FeatureImpact,
    ProjectContextParser,
    analyze_feature_risks,
)
from .utils import resolve_code_graph_db


class ImpactAnalyzer:
    """Caller-based impact analyzer backed by the code-graph SQLite index."""

    def __init__(self, bridge: CodeGraphBridge, project_path: str) -> None:
        self.bridge = bridge
        self.project_path = Path(project_path)
        self.context = ProjectContextParser(project_path)
        self._db_path = resolve_code_graph_db(project_path)

    def analyze_impact(self, symbol: str, depth: int = 1) -> Dict:
        """Analyze impact of changing a function, method or class.

        `symbol` may be 'name', 'Class.method', 'path/file.py:name' or
        'package.module.name'. When several definitions match, the best one
        is analyzed and the rest are listed in other_definitions. A class
        counts callers of the class and of its methods. depth > 1 also lists
        indirect callers (callers of callers) up to that many levels.
        """
        if not self._db_path.exists():
            return self._not_found(symbol, "No code-graph database found. Run trellis sync first.")

        with gq.connect(self._db_path) as conn:
            matches = gq.find_symbol(conn, symbol, gq.SYMBOL_TYPES)
            if not matches:
                return self._not_found(symbol, f"Function '{symbol}' not found in index")
            root = matches[0]
            root["source"] = gq.code_content(conn, root["id"]) or ""
            targets = [root["id"]]
            if root["type"] == "class":
                targets += gq.class_methods(conn, root)
            found = gq.callers_of_all(conn, targets)
            caller_ids = {c["node_id"] for c in found["callers"]}
            same_file = [
                f
                for f in gq.functions_in_file(conn, root["file_path"])
                if f["node_id"] not in targets and f["node_id"] not in caller_ids
            ]
            indirect = gq.indirect_callers(conn, targets, depth) if depth > 1 else []

        callers = found["callers"]
        caller_files = {c["file_path"] for c in callers}
        affected = callers + same_file

        feature_impacts = self._analyze_feature_impacts(root, affected)
        risk_level = self._calculate_risk(len(callers), len(caller_files), feature_impacts)
        risk_note = ""
        if not callers and found["ambiguous"]:
            # Only unconfirmed by-name callers: don't call that low risk.
            unsure_files = {c["file_path"] for c in found["ambiguous"]}
            risk_level = self._calculate_risk(len(found["ambiguous"]), len(unsure_files), feature_impacts)
            risk_note = "No confirmed callers; risk is based on unconfirmed (ambiguous) callers."

        return {
            "symbol": symbol,
            "found": True,
            "resolved": gq.describe(root),
            "line": root["line"],
            "other_definitions": [gq.describe(m) for m in matches[1:10]],
            "risk_level": risk_level,
            "risk_note": risk_note,
            "caller_count": len(callers),
            "indirect_callers": indirect,
            "affected_functions_count": len(affected),
            "affected_files_count": len(caller_files),
            "callers": callers,
            "test_callers": found["test_callers"],
            "ambiguous_callers": found["ambiguous"],
            "same_file_functions": same_file,
            "affected_functions": affected,
            "references": [],
            "feature_impacts": [
                {
                    "feature_name": fi.feature_name,
                    "impacted_functions": fi.impacted_functions,
                    "affected_decisions": [
                        {"id": d.decision_id, "description": d.description}
                        for d in fi.affected_decisions
                    ],
                    "risk_flags": fi.risk_flags,
                    "recommended_actions": fi.recommended_actions,
                }
                for fi in feature_impacts
            ],
            "divergence_warnings": self._check_divergence(root),
            "development_pointers": self._get_development_pointers(root, feature_impacts),
        }

    @staticmethod
    def _not_found(symbol: str, error: str) -> Dict:
        return {
            "symbol": symbol,
            "found": False,
            "error": error,
            "hint": "Use 'path/to/file.py:function' or 'Class.method'; "
            "trellis_search_code finds the exact name.",
            "risk_level": "unknown",
            "caller_count": 0,
            "affected_functions_count": 0,
            "affected_files_count": 0,
            "callers": [],
            "feature_impacts": [],
            "divergence_warnings": [],
            "development_pointers": [],
        }

    def _analyze_feature_impacts(
        self, root: Dict, affected_functions: List[Dict]
    ) -> List[FeatureImpact]:
        """Group the root and affected functions by feature and analyze risks."""
        feature_impacts: Dict[str, FeatureImpact] = {}
        self._add_function_to_impact(root, feature_impacts, is_root=True)

        # Add all affected functions
        for func in affected_functions:
            self._add_function_to_impact(func, feature_impacts)

        # Analyze risks for each feature
        for impact in feature_impacts.values():
            analyze_feature_risks(self.context, impact)

        return list(feature_impacts.values())

    def _add_function_to_impact(
        self,
        func: Dict,
        feature_impacts: Dict[str, FeatureImpact],
        is_root: bool = False,
    ) -> None:
        """Add a function to its feature's impact."""
        file_path = func.get("file_path", "")
        if not file_path:
            file_path = func.get("file", "")  # Handle different field names

        feature = self.context.get_feature_for_file(file_path)

        func_info = {
            "name": func.get("name", func.get("qualified_name", "unknown")),
            "qualified_name": func.get("qualified_name", ""),
            "file_path": file_path,
            "is_root": is_root,
        }

        if feature:
            feature_name = feature.feature_name
            if feature_name not in feature_impacts:
                feature_impacts[feature_name] = FeatureImpact(feature_name=feature_name)
            feature_impacts[feature_name].impacted_functions.append(func_info)
        else:
            if "unassigned" not in feature_impacts:
                feature_impacts["unassigned"] = FeatureImpact(
                    feature_name="unassigned",
                    risk_flags=["Function not mapped to any feature in project.md"],
                )
            feature_impacts["unassigned"].impacted_functions.append(func_info)

    def _calculate_risk(
        self, affected_count: int, file_count: int, feature_impacts: List[FeatureImpact]
    ) -> str:
        """Calculate risk level based on blast radius."""
        if affected_count == 0:
            return "LOW"

        # Cross-feature impact increases risk
        cross_feature = len(
            [fi for fi in feature_impacts if fi.feature_name != "unassigned"]
        )

        if affected_count > 20 or cross_feature > 3 or file_count > 10:
            return "HIGH"
        elif affected_count > 5 or cross_feature > 1 or file_count > 3:
            return "MEDIUM"
        return "LOW"

    def _check_divergence(self, node: Dict) -> List[str]:
        """Check if function diverges from feature spec."""
        warnings = []
        symbol = node["qualified_name"]
        file_path = node.get("file_path", "")
        feature = self.context.get_feature_for_file(file_path)

        if not feature:
            warnings.append(f"Function '{symbol}' is not mapped to any feature")
            return warnings

        # Check constraints
        source = node.get("source", "")
        for constraint in feature.constraints:
            if "must not" in constraint.lower():
                forbidden = constraint.lower().split("must not")[-1].strip()
                if forbidden in source.lower():
                    warnings.append(f"DIVERGENCE: May violate constraint: {constraint}")

        if feature.status == "deprecated":
            warnings.append(
                f"DIVERGENCE: Function in deprecated feature '{feature.feature_name}'"
            )

        return warnings

    def _get_development_pointers(
        self, node: Dict, feature_impacts: List[FeatureImpact]
    ) -> List[str]:
        """Generate actionable development pointers."""
        pointers = []
        file_path = node.get("file_path", "")
        feature = self.context.get_feature_for_file(file_path)

        if feature:
            pointers.append(f"Feature: {feature.feature_name} - {feature.description}")

            for constraint in feature.constraints:
                pointers.append(f"Constraint: {constraint}")

            for decision in feature.decisions:
                pointers.append(
                    f"Decision {decision.decision_id}: {decision.description}"
                )
                if decision.rationale:
                    pointers.append(f"  Why: {decision.rationale}")
                for constraint in decision.constraints:
                    pointers.append(f"  Must maintain: {constraint}")

            if feature.dependencies:
                pointers.append(f"Dependencies: {', '.join(feature.dependencies)}")

            if feature.status == "deprecated":
                pointers.append(
                    "WARNING: Feature is deprecated - changes should be for migration only"
                )

        # Add cross-feature impact notes
        other_features = [
            fi
            for fi in feature_impacts
            if fi.feature_name
            not in (feature.feature_name if feature else "", "unassigned")
        ]
        if other_features:
            pointers.append(
                f"\nCross-feature impact: affects {len(other_features)} other features"
            )
            for fi in other_features:
                pointers.append(
                    f"  - {fi.feature_name}: {len(fi.impacted_functions)} functions"
                )

        return pointers
