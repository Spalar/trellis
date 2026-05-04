"""Feature Impact Analysis Layer.

Analyzes how code changes impact feature-level decisions and constraints.
Uses code-graph-mcp for technical analysis, adds feature context.

Key concept:
- Technical impact: "This function calls 5 other functions" (code-graph-mcp)
- Feature impact: "This change affects Feature X which requires Y constraint"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .bridge import CodeGraphBridge


@dataclass
class FeatureDecision:
    """A decision recorded for a feature."""
    decision_id: str
    description: str
    rationale: str = ""
    constraints: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)


@dataclass
class FeatureSpec:
    """Feature specification from project.md."""
    feature_name: str
    description: str = ""
    decisions: List[FeatureDecision] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    file_patterns: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    owner: str = ""  # Team or person responsible
    status: str = "active"  # active, deprecated, planned


@dataclass
class FeatureImpact:
    """Result of feature impact analysis."""
    feature_name: str
    impacted_functions: List[Dict] = field(default_factory=list)
    affected_decisions: List[FeatureDecision] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)
    divergence_warnings: List[str] = field(default_factory=list)


class ProjectContextParser:
    """Parse project.md to extract feature specifications."""
    
    def __init__(self, project_path: str) -> None:
        self.project_path = Path(project_path)
        self.spec_file = self.project_path / "project.md"
        self.features: Dict[str, FeatureSpec] = {}
        self._parse()
    
    def _parse(self) -> None:
        """Parse project.md and extract features."""
        if not self.spec_file.exists():
            return
        
        content = self.spec_file.read_text(encoding="utf-8")
        
        # Parse features from markdown
        # Format:
        # ## Feature: Authentication
        # Description: Handles user authentication
        # 
        # ### Decisions
        # - DEC-001: Use JWT tokens (because: stateless)
        #   - Constraint: Token expiry must be < 24h
        #   - Constraint: Refresh tokens stored in httpOnly cookies
        #
        # ### Files
        # - src/auth/**
        # - src/middleware/auth*
        #
        # ### Dependencies
        # - Feature: User Management
        
        current_feature = None
        current_section = None
        current_decision = None
        line_count = 0
        
        for line in content.splitlines():
            line_count += 1
            line_stripped = line.strip()
            
            # Feature header - more flexible matching
            if line_stripped.startswith("## Feature:") or line_stripped.startswith("## Feature "):
                # Extract feature name after "Feature:"
                if ":" in line_stripped:
                    feature_name = line_stripped.split(":", 1)[1].strip()
                else:
                    feature_name = line_stripped.replace("## Feature", "").strip()
                
                current_feature = FeatureSpec(feature_name=feature_name)
                self.features[feature_name] = current_feature
                current_section = None
                current_decision = None
            
            # Skip if no feature yet
            if not current_feature:
                continue
            
            # Section headers
            elif line_stripped.startswith("### "):
                section_name = line_stripped.replace("###", "").strip().lower()
                current_section = section_name
                current_decision = None
            
            # Description (before any section, non-empty, not a list item)
            elif line_stripped and not current_section and not line_stripped.startswith("-") and not line_stripped.startswith("#"):
                if not current_feature.description:
                    current_feature.description = line_stripped
                else:
                    current_feature.description += " " + line_stripped
            
            # Decisions
            elif current_section == "decisions" and line_stripped.startswith("-"):
                # Parse decision: - DEC-001: Description (because: rationale)
                decision_match = re.match(r"-\s*(\w+-\d+):\s*(.+?)(?:\s*\(because:\s*(.+)\))?\s*$", line_stripped)
                if decision_match:
                    decision_id = decision_match.group(1)
                    description = decision_match.group(2)
                    rationale = decision_match.group(3) or ""
                    
                    current_decision = FeatureDecision(
                        decision_id=decision_id,
                        description=description,
                        rationale=rationale
                    )
                    current_feature.decisions.append(current_decision)
            
            # Constraints under decisions
            elif current_decision and line_stripped.startswith("-") and "constraint:" in line_stripped.lower():
                constraint = re.sub(r"-\s*constraint:\s*", "", line_stripped, flags=re.IGNORECASE).strip()
                current_decision.constraints.append(constraint)
            
            # Constraints under feature
            elif current_section == "constraints" and line_stripped.startswith("-"):
                constraint = line_stripped.replace("-", "", 1).strip()
                current_feature.constraints.append(constraint)
            
            # File patterns
            elif current_section in ("files", "file patterns") and line_stripped.startswith("-"):
                pattern = line_stripped.replace("-", "", 1).strip()
                current_feature.file_patterns.append(pattern)
            
            # Dependencies
            elif current_section == "dependencies" and line_stripped.startswith("-"):
                dep = line_stripped.replace("-", "", 1).strip()
                if dep.startswith("Feature:"):
                    dep = dep.replace("Feature:", "").strip()
                current_feature.dependencies.append(dep)
        
        # Parsing complete
    
    def get_feature_for_file(self, file_path: str) -> Optional[FeatureSpec]:
        """Find which feature owns a file.
        
        Args:
            file_path: Path to source file
            
        Returns:
            FeatureSpec if found, None otherwise
        """
        for feature in self.features.values():
            for pattern in feature.file_patterns:
                # Convert glob to regex
                regex_pattern = pattern.replace("**", ".*").replace("*", "[^/]*")
                if re.search(regex_pattern, file_path):
                    return feature
        return None
    
    def get_all_features(self) -> Dict[str, FeatureSpec]:
        """Get all parsed features."""
        return self.features


class FeatureImpactAnalyzer:
    """Analyzes feature-level impact of code changes.
    
    Uses code-graph-mcp for technical analysis,
    adds feature context from project.md.
    """
    
    def __init__(self, bridge: CodeGraphBridge, project_path: str) -> None:
        self.bridge = bridge
        self.project_path = Path(project_path)
        self.context = ProjectContextParser(project_path)
    
    def analyze_feature_impact(self, symbol: str, depth: int = 2) -> List[FeatureImpact]:
        """Analyze impact of changing a symbol at feature level.
        
        Returns:
            List of FeatureImpact objects, one per affected feature
        """
        # 1. Get technical impact from code-graph-mcp
        technical_impact = self.bridge.analyze_impact(symbol, depth=depth)
        
        # 2. Get affected functions
        affected_functions = technical_impact.get("affected_functions", [])
        
        # 3. Group by feature
        feature_impacts: Dict[str, FeatureImpact] = {}
        
        # Add the changed function itself
        root_func = self.bridge.get_ast_node(symbol)
        if root_func:
            self._add_function_to_impact(root_func, feature_impacts, is_root=True)
        
        # Add all affected functions
        for func in affected_functions:
            self._add_function_to_impact(func, feature_impacts)
        
        # 4. Analyze each feature for risks
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
            return
        
        # Find which feature this file belongs to
        feature = self.context.get_feature_for_file(file_path)
        
        if feature:
            feature_name = feature.feature_name
            
            if feature_name not in feature_impacts:
                feature_impacts[feature_name] = FeatureImpact(
                    feature_name=feature_name
                )
            
            func_info = {
                "name": func.get("name", "unknown"),
                "qualified_name": func.get("qualified_name", ""),
                "file_path": file_path,
                "is_root": is_root,
            }
            
            feature_impacts[feature_name].impacted_functions.append(func_info)
        
        else:
            # No feature found - might be a shared/util file
            if "unassigned" not in feature_impacts:
                feature_impacts["unassigned"] = FeatureImpact(
                    feature_name="unassigned",
                    risk_flags=["Function not mapped to any feature in project.md"]
                )
            
            feature_impacts["unassigned"].impacted_functions.append({
                "name": func.get("name", "unknown"),
                "file_path": file_path,
                "is_root": is_root,
            })
    
    def _analyze_feature_risks(self, impact: FeatureImpact) -> None:
        """Analyze risks for a feature based on its impacted functions."""
        feature = self.context.features.get(impact.feature_name)
        if not feature:
            return
        
        # Check for affected decisions
        for decision in feature.decisions:
            # If any constraint is related to the changed files, flag it
            for constraint in decision.constraints:
                for func in impact.impacted_functions:
                    # Simple keyword matching - could be more sophisticated
                    if any(keyword in constraint.lower() for keyword in 
                          ["auth", "security", "performance", "api", "contract"]):
                        impact.affected_decisions.append(decision)
                        break
        
        # Check for feature-level risk flags
        root_funcs = [f for f in impact.impacted_functions if f.get("is_root")]
        if root_funcs:
            impact.risk_flags.append(f"Root function change affects {len(impact.impacted_functions)} functions")
        
        # Check if feature is deprecated
        if feature.status == "deprecated":
            impact.risk_flags.append("Feature is deprecated - changes may indicate migration needed")
        
        # Check dependencies
        if feature.dependencies:
            impact.risk_flags.append(f"Feature has {len(feature.dependencies)} dependencies: {', '.join(feature.dependencies)}")
        
        # Generate recommendations
        if impact.affected_decisions:
            impact.recommended_actions.append(
                "Review affected decisions in project.md before committing changes"
            )
        
        if len(impact.impacted_functions) > 10:
            impact.recommended_actions.append(
                "Large blast radius - consider breaking into smaller changes"
            )
        
        if not feature.file_patterns:
            impact.recommended_actions.append(
                "Feature has no file patterns defined - add to project.md for better tracking"
            )
    
    def check_divergence(self, symbol: str) -> List[str]:
        """Check if a function diverges from its feature spec.
        
        Returns:
            List of divergence warnings
        """
        warnings = []
        
        # Get function details
        node = self.bridge.get_ast_node(symbol)
        if not node:
            return warnings
        
        file_path = node.get("file_path", "")
        feature = self.context.get_feature_for_file(file_path)
        
        if not feature:
            warnings.append(f"Function '{symbol}' is not mapped to any feature")
            return warnings
        
        # Check if function violates constraints
        source_code = node.get("source", "")
        for constraint in feature.constraints:
            # Simple constraint checking - can be extended
            if "must not" in constraint.lower():
                forbidden = constraint.lower().split("must not")[-1].strip()
                if forbidden in source_code.lower():
                    warnings.append(
                        f"DIVERGENCE: Function may violate constraint: {constraint}"
                    )
        
        # Check if feature is marked deprecated
        if feature.status == "deprecated":
            warnings.append(
                f"DIVERGENCE: Function in deprecated feature '{feature.feature_name}'"
            )
        
        return warnings
    
    def get_feature_context(self, symbol: str) -> Optional[Dict]:
        """Get feature context for a function.
        
        Returns:
            Dict with feature details, decisions, constraints
        """
        node = self.bridge.get_ast_node(symbol)
        if not node:
            return None
        
        file_path = node.get("file_path", "")
        feature = self.context.get_feature_for_file(file_path)
        
        if not feature:
            return None
        
        return {
            "feature_name": feature.feature_name,
            "description": feature.description,
            "status": feature.status,
            "owner": feature.owner,
            "decisions": [
                {
                    "id": d.decision_id,
                    "description": d.description,
                    "rationale": d.rationale,
                    "constraints": d.constraints,
                }
                for d in feature.decisions
            ],
            "constraints": feature.constraints,
            "dependencies": feature.dependencies,
            "file_patterns": feature.file_patterns,
        }
    
    def get_development_pointers(self, symbol: str) -> List[str]:
        """Get development pointers for a function.
        
        These are actionable insights for the coding agent.
        
        Returns:
            List of development pointers
        """
        pointers = []
        
        # Get feature context
        context = self.get_feature_context(symbol)
        if not context:
            pointers.append("No feature context found - consider adding to project.md")
            return pointers
        
        # Add feature description
        pointers.append(f"Feature: {context['feature_name']} - {context['description']}")
        
        # Add constraints
        for constraint in context["constraints"]:
            pointers.append(f"Constraint: {constraint}")
        
        # Add decisions
        for decision in context["decisions"]:
            pointers.append(f"Decision {decision['id']}: {decision['description']}")
            if decision["rationale"]:
                pointers.append(f"  Why: {decision['rationale']}")
            for constraint in decision["constraints"]:
                pointers.append(f"  Must maintain: {constraint}")
        
        # Add dependencies
        if context["dependencies"]:
            pointers.append(f"Dependencies: {', '.join(context['dependencies'])}")
        
        # Check status
        if context["status"] == "deprecated":
            pointers.append("WARNING: Feature is deprecated - changes should be for migration only")
        elif context["status"] == "planned":
            pointers.append("INFO: Feature is planned - ensure changes align with roadmap")
        
        return pointers
    
    def generate_feature_report(self, symbol: str, depth: int = 2) -> Dict:
        """Generate comprehensive feature impact report.
        
        Returns:
            Structured report with technical + feature impact
        """
        # Get technical impact
        technical = self.bridge.analyze_impact(symbol, depth=depth)
        
        # Get feature impacts
        feature_impacts = self.analyze_feature_impact(symbol, depth=depth)
        
        # Get divergence warnings
        divergence = self.check_divergence(symbol)
        
        # Get development pointers
        pointers = self.get_development_pointers(symbol)
        
        return {
            "symbol": symbol,
            "technical_impact": technical,
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
            "summary": self._generate_summary(symbol, feature_impacts, divergence)
        }
    
    def _generate_summary(
        self,
        symbol: str,
        feature_impacts: List[FeatureImpact],
        divergence: List[str]
    ) -> str:
        """Generate human-readable summary."""
        lines = [f"Feature Impact Analysis: {symbol}"]
        lines.append("")
        
        total_features = len([fi for fi in feature_impacts if fi.feature_name != "unassigned"])
        total_funcs = sum(len(fi.impacted_functions) for fi in feature_impacts)
        
        lines.append(f"Affects {total_funcs} functions across {total_features} features")
        
        for fi in feature_impacts:
            if fi.feature_name == "unassigned":
                continue
            
            lines.append(f"\n📦 Feature: {fi.feature_name}")
            lines.append(f"   Functions: {len(fi.impacted_functions)}")
            
            if fi.affected_decisions:
                lines.append(f"   ⚠️  Affected decisions: {len(fi.affected_decisions)}")
            
            if fi.risk_flags:
                for flag in fi.risk_flags:
                    lines.append(f"   ⚠️  {flag}")
            
            if fi.recommended_actions:
                for action in fi.recommended_actions:
                    lines.append(f"   💡 {action}")
        
        if divergence:
            lines.append("\n🚨 Divergence Warnings:")
            for warning in divergence:
                lines.append(f"   {warning}")
        
        return "\n".join(lines)
