"""Enhanced Impact Analysis for Trellis.

Builds comprehensive impact analysis combining:
1. Symbol references from code-graph-mcp (find_references)
2. File-level function discovery via SQLite
3. Feature mapping from project.md
4. Cross-feature dependency analysis
5. Divergence detection and development pointers

This overcomes the limitation of code-graph-mcp which only
has import edges (not call edges) in the current indexing.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

from .bridge import CodeGraphBridge
from .feature_impact import (
    FeatureDecision,
    FeatureImpact,
    FeatureSpec,
    ProjectContextParser,
)


class ImpactAnalyzer:
    """Comprehensive impact analyzer that builds affected functions
    from available data sources.
    """
    
    def __init__(self, bridge: CodeGraphBridge, project_path: str) -> None:
        self.bridge = bridge
        self.project_path = Path(project_path)
        self.context = ProjectContextParser(project_path)
        self._db_path = self.project_path / ".code-graph" / "index.db"
    
    def analyze_impact(self, symbol: str, depth: int = 2) -> Dict:
        """Analyze impact of changing a symbol.
        
        Returns comprehensive report with:
        - callers: Functions that directly call this symbol
        - affected_functions: All functions that may be impacted (callers + same file)
        - risk_level: LOW/MEDIUM/HIGH based on blast radius
        - feature_impacts: Per-feature impact analysis
        - divergence_warnings: Spec vs implementation mismatches
        - development_pointers: Actionable guidance for developer
        """
        # 1. Get symbol info - try qualified name first, then fall back to simple name + file lookup
        symbol_node = self.bridge.get_ast_node(symbol)
        if not symbol_node or 'text' in symbol_node or 'error' in symbol_node:
            symbol_node = self._get_node_by_qualified_name(symbol)
        
        symbol_file = symbol_node.get("file_path", "") if symbol_node else ""
        
        # 2. Find callers using our custom call graph edges
        caller_functions = self._get_callers(symbol)
        caller_files = {f["file_path"] for f in caller_functions}
        
        # 3. Find functions in the same file (for context)
        same_file_functions = []
        if symbol_file:
            same_file_functions = self._get_functions_in_files({symbol_file})
            # Filter out the symbol itself (check both qualified and simple name)
            symbol_simple = symbol.split('.')[-1]
            existing_callers = {c["qualified_name"] for c in caller_functions}
            same_file_functions = [f for f in same_file_functions 
                                   if f["qualified_name"] != symbol 
                                   and f["name"] != symbol_simple
                                   and f["qualified_name"] not in existing_callers]
        
        # 4. Combine and deduplicate
        seen = set()
        all_functions = []
        for func in caller_functions + same_file_functions:
            key = func["qualified_name"]
            if key not in seen:
                seen.add(key)
                all_functions.append(func)
        
        # 5. Get feature mapping for all functions
        feature_impacts = self._analyze_feature_impacts(symbol, all_functions)
        
        # 6. Calculate risk level based on actual callers
        risk_level = self._calculate_risk(
            len(caller_functions),
            len(caller_files),
            feature_impacts
        )
        
        # 8. Get divergence and pointers
        divergence = self._check_divergence(symbol)
        pointers = self._get_development_pointers(symbol, feature_impacts)
        
        return {
            "symbol": symbol,
            "risk_level": risk_level,
            "caller_count": len(caller_functions),
            "affected_functions_count": len(all_functions),
            "affected_files_count": len(caller_files),
            "callers": caller_functions,
            "same_file_functions": same_file_functions,
            "affected_functions": all_functions,
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
            "divergence_warnings": divergence,
            "development_pointers": pointers,
        }
    
    def _get_references(self, symbol: str) -> List[Dict]:
        """Get all references to a symbol using code-graph-mcp."""
        try:
            result = self.bridge._call("find_references", symbol_name=symbol)
            if isinstance(result, dict):
                return result.get("references", [])
        except Exception:
            pass
        return []
    
    def _get_callers(self, symbol: str) -> List[Dict]:
        """Find functions that call this symbol using our custom call edges."""
        if not self._db_path.exists():
            return []
        
        # Extract simple name for matching
        simple_name = symbol.split('.')[-1]
        
        callers = []
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            # Find all functions that have a 'calls' edge to this symbol
            cursor.execute("""
                SELECT DISTINCT s.id, s.name, s.qualified_name, f.path, s.start_line, s.type, s.signature
                FROM edges e
                JOIN nodes t ON e.target_id = t.id
                JOIN nodes s ON e.source_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE e.relation = 'calls'
                  AND (t.name = ? OR t.qualified_name = ?)
                  AND s.type IN ('function', 'method')
                  AND s.name NOT LIKE 'test_%'
                  AND s.name != '__init__'
                  AND s.name != '<module>'
            """, (simple_name, symbol))
            
            for row in cursor.fetchall():
                callers.append({
                    "node_id": row[0],
                    "name": row[1],
                    "qualified_name": row[2] or row[1],
                    "file_path": row[3],
                    "line": row[4],
                    "type": row[5],
                    "signature": row[6] or "",
                })
            
            conn.close()
        except Exception:
            pass
        
        return callers
    
    def _get_node_by_qualified_name(self, qualified_name: str) -> Optional[Dict]:
        """Look up a node by qualified name from SQLite."""
        if not self._db_path.exists():
            return None
        
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            # Try exact qualified name match
            cursor.execute("""
                SELECT n.name, n.qualified_name, f.path, n.start_line, n.type, n.signature
                FROM nodes n
                JOIN files f ON n.file_id = f.id
                WHERE n.qualified_name = ?
                  AND n.type IN ('function', 'method')
                LIMIT 1
            """, (qualified_name,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    "name": row[0],
                    "qualified_name": row[1] or row[0],
                    "file_path": row[2],
                    "line": row[3],
                    "type": row[4],
                    "signature": row[5] or "",
                }
        except Exception:
            pass
        
        return None
    
    def _get_functions_in_files(self, file_paths: Set[str]) -> List[Dict]:
        """Query SQLite for all functions in given files."""
        if not self._db_path.exists() or not file_paths:
            return []
        
        functions = []
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            # Build placeholders for IN clause
            placeholders = ','.join('?' * len(file_paths))
            cursor.execute(f"""
                SELECT n.name, n.qualified_name, f.path, n.start_line, n.type, n.signature
                FROM nodes n
                JOIN files f ON n.file_id = f.id
                WHERE f.path IN ({placeholders})
                  AND n.type IN ('function', 'method')
                  AND n.name NOT LIKE 'test_%'
                  AND n.name != '__init__'
                  AND n.name != '<module>'
                ORDER BY f.path, n.start_line
            """, tuple(file_paths))
            
            for row in cursor.fetchall():
                functions.append({
                    "name": row[0],
                    "qualified_name": row[1] or row[0],
                    "file_path": row[2],
                    "line": row[3],
                    "type": row[4],
                    "signature": row[5] or "",
                })
            
            conn.close()
        except Exception:
            pass
        
        return functions
    
    def _analyze_feature_impacts(
        self,
        symbol: str,
        affected_functions: List[Dict]
    ) -> List[FeatureImpact]:
        """Group affected functions by feature and analyze risks."""
        feature_impacts: Dict[str, FeatureImpact] = {}
        
        # Add the root symbol itself
        root_node = self.bridge.get_ast_node(symbol)
        if not root_node or 'text' in root_node or 'error' in root_node:
            root_node = self._get_node_by_qualified_name(symbol)
        if root_node:
            self._add_function_to_impact(root_node, feature_impacts, is_root=True)
        
        # Add all affected functions
        for func in affected_functions:
            self._add_function_to_impact(func, feature_impacts)
        
        # Analyze risks for each feature
        for impact in feature_impacts.values():
            self._analyze_feature_risks(impact)
        
        return list(feature_impacts.values())
    
    def _add_function_to_impact(
        self,
        func: Dict,
        feature_impacts: Dict[str, FeatureImpact],
        is_root: bool = False
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
                    risk_flags=["Function not mapped to any feature in project.md"]
                )
            feature_impacts["unassigned"].impacted_functions.append(func_info)
    
    def _analyze_feature_risks(self, impact: FeatureImpact) -> None:
        """Analyze risks for a feature based on impacted functions."""
        feature = self.context.features.get(impact.feature_name)
        if not feature:
            return
        
        # Check for affected decisions
        for decision in feature.decisions:
            for constraint in decision.constraints:
                for func in impact.impacted_functions:
                    if any(keyword in constraint.lower() for keyword in 
                          ["auth", "security", "performance", "api", "contract"]):
                        impact.affected_decisions.append(decision)
                        break
        
        # Risk flags
        root_funcs = [f for f in impact.impacted_functions if f.get("is_root")]
        if root_funcs:
            impact.risk_flags.append(
                f"Root function change affects {len(impact.impacted_functions)} functions"
            )
        
        if feature.status == "deprecated":
            impact.risk_flags.append("Feature is deprecated - changes may indicate migration needed")
        
        if feature.dependencies:
            impact.risk_flags.append(
                f"Feature has {len(feature.dependencies)} dependencies: {', '.join(feature.dependencies)}"
            )
        
        # Recommendations
        if impact.affected_decisions:
            impact.recommended_actions.append(
                "Review affected decisions in project.md before committing changes"
            )
        
        if len(impact.impacted_functions) > 10:
            impact.recommended_actions.append(
                "Large blast radius - consider breaking into smaller changes"
            )
    
    def _calculate_risk(
        self,
        affected_count: int,
        file_count: int,
        feature_impacts: List[FeatureImpact]
    ) -> str:
        """Calculate risk level based on blast radius."""
        if affected_count == 0:
            return "LOW"
        
        # Cross-feature impact increases risk
        cross_feature = len([fi for fi in feature_impacts if fi.feature_name != "unassigned"])
        
        if affected_count > 20 or cross_feature > 3 or file_count > 10:
            return "HIGH"
        elif affected_count > 5 or cross_feature > 1 or file_count > 3:
            return "MEDIUM"
        return "LOW"
    
    def _check_divergence(self, symbol: str) -> List[str]:
        """Check if function diverges from feature spec."""
        warnings = []
        
        node = self.bridge.get_ast_node(symbol)
        if not node or 'text' in node or 'error' in node:
            node = self._get_node_by_qualified_name(symbol)
        if not node:
            return warnings
        
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
            warnings.append(f"DIVERGENCE: Function in deprecated feature '{feature.feature_name}'")
        
        return warnings
    
    def _get_development_pointers(
        self,
        symbol: str,
        feature_impacts: List[FeatureImpact]
    ) -> List[str]:
        """Generate actionable development pointers."""
        pointers = []
        
        # Get symbol's feature
        node = self.bridge.get_ast_node(symbol)
        if not node or 'text' in node or 'error' in node:
            node = self._get_node_by_qualified_name(symbol)
        if not node:
            pointers.append("No feature context found - consider adding to project.md")
            return pointers
        
        file_path = node.get("file_path", "")
        feature = self.context.get_feature_for_file(file_path)
        
        if feature:
            pointers.append(f"Feature: {feature.feature_name} - {feature.description}")
            
            for constraint in feature.constraints:
                pointers.append(f"Constraint: {constraint}")
            
            for decision in feature.decisions:
                pointers.append(f"Decision {decision.decision_id}: {decision.description}")
                if decision.rationale:
                    pointers.append(f"  Why: {decision.rationale}")
                for constraint in decision.constraints:
                    pointers.append(f"  Must maintain: {constraint}")
            
            if feature.dependencies:
                pointers.append(f"Dependencies: {', '.join(feature.dependencies)}")
            
            if feature.status == "deprecated":
                pointers.append("WARNING: Feature is deprecated - changes should be for migration only")
        
        # Add cross-feature impact notes
        other_features = [fi for fi in feature_impacts if fi.feature_name not in (feature.feature_name if feature else "", "unassigned")]
        if other_features:
            pointers.append(f"\nCross-feature impact: affects {len(other_features)} other features")
            for fi in other_features:
                pointers.append(f"  - {fi.feature_name}: {len(fi.impacted_functions)} functions")
        
        return pointers
