from __future__ import annotations

import re
import subprocess
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Set

from models import (
    BoundaryMap,
    Breakpoint,
    CoverageGap,
    DiffChange,
    DiffImpactReport,
    FunctionRecord,
    Hotspot,
    HotspotReport,
    ImpactEdge,
    ImpactReport,
    RiskGroup,
    TestSuggestion,
)
from store import GraphStore


class ImpactAnalyzer:
    """Enhanced impact analysis with confidence scoring, risk grouping, and refactor awareness."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Change-intent driven impact analysis
    # ------------------------------------------------------------------
    def analyze_impact(
        self,
        project_id: str,
        root_function: FunctionRecord,
        change_summary: str,
        depth_mode: str = "standard",
    ) -> ImpactReport:
        """Analyze impact with change-intent awareness and multi-depth modes."""
        intent = self._parse_change_intent(change_summary)
        impacted = self._collect_impacted(project_id, root_function, intent, depth_mode)
        edges = self._build_edges(project_id, root_function, impacted, intent)
        risk_groups = self._group_by_risk(project_id, impacted, edges, intent)
        breakpoints = self._detect_breakpoints(root_function, impacted, intent)
        tests = self._suggest_tests(project_id, impacted)
        gaps = self._find_coverage_gaps(project_id, impacted)

        # Calculate overall confidence
        avg_confidence = sum(e.confidence for e in edges) / len(edges) if edges else 0.0

        all_functions = sorted({e.target for e in edges})
        all_features = sorted({
            fn.feature_name
            for fn in self.store.load_functions_batch(project_id, all_functions)
        })

        risk_level = self._overall_risk(risk_groups)

        return ImpactReport(
            project_id=project_id,
            root_function=root_function.function_path,
            change_summary=change_summary,
            change_intent=intent.get("action", "") + " " + intent.get("target", ""),
            impacted_features=all_features,
            impacted_functions=all_functions,
            risk_groups=risk_groups,
            risk_level=risk_level,
            edges=edges,
            breakpoints=breakpoints,
            test_suggestions=tests,
            coverage_gaps=gaps,
            suggestions=self._generate_suggestions(intent, risk_groups, breakpoints),
            confidence=round(avg_confidence, 2),
        )

    def _parse_change_intent(self, change_summary: str) -> dict:
        """Parse natural language change intent into structured action."""
        intent = {"action": "", "target": "", "broad": False}
        if not change_summary:
            return intent

        text = change_summary.lower()

        # Detect action
        actions = {
            "add": ["add", "introduce", "create", "new"],
            "remove": ["remove", "delete", "drop", "deprecate"],
            "modify": ["change", "update", "alter", "refactor", "rename", "move"],
            "split": ["split", "extract", "separate"],
            "merge": ["merge", "combine", "consolidate"],
        }
        for action, keywords in actions.items():
            if any(kw in text for kw in keywords):
                intent["action"] = action
                break
        if not intent["action"]:
            intent["action"] = "modify"

        # Detect target
        targets = {
            "field": ["field", "property", "attribute", "column", "parameter", "param", "arg"],
            "method": ["method", "function", "endpoint", "handler"],
            "class": ["class", "service", "component", "module"],
            "schema": ["schema", "dto", "model", "type", "interface"],
            "api": ["api", "endpoint", "route", "contract"],
            "config": ["config", "setting", "env", "configuration"],
        }
        for target, keywords in targets.items():
            if any(kw in text for kw in keywords):
                intent["target"] = target
                break

        # Broad impact detection
        broad_keywords = ["api", "interface", "schema", "global", "shared", "core", "contract"]
        intent["broad"] = any(kw in text for kw in broad_keywords)

        return intent

    # ------------------------------------------------------------------
    # Multi-depth dependency collection
    # ------------------------------------------------------------------
    def _collect_impacted(
        self,
        project_id: str,
        root: FunctionRecord,
        intent: dict,
        depth_mode: str,
    ) -> Dict[str, dict]:
        """Collect impacted functions with multi-depth awareness."""
        impacted: Dict[str, dict] = {}

        # Standard: call graph traversal
        callers = self._transitive_callers(project_id, root.function_path)
        for func_path in callers:
            impacted[func_path] = {
                "confidence": 0.9,
                "reason": "Direct call chain dependency",
                "dimension": "call_graph",
            }

        if depth_mode in {"deep", "full"}:
            # Add data flow neighbors
            data_flow = self._data_flow_neighbors(project_id, root)
            for func_path, reason in data_flow.items():
                if func_path not in impacted:
                    impacted[func_path] = {
                        "confidence": 0.6,
                        "reason": reason,
                        "dimension": "data_flow",
                    }

        if depth_mode == "full":
            # Add configuration coupling
            config_coupled = self._config_coupling(project_id, root)
            for func_path, reason in config_coupled.items():
                if func_path not in impacted:
                    impacted[func_path] = {
                        "confidence": 0.4,
                        "reason": reason,
                        "dimension": "config",
                    }

        # Intent-based expansion for broad changes
        if intent.get("broad"):
            all_funcs = self.store.list_functions(project_id)
            for func in all_funcs:
                if func.function_path not in impacted and not func.function_path.startswith("_"):
                    impacted[func.function_path] = {
                        "confidence": 0.3,
                        "reason": f"Potentially affected by {intent['action']} {intent['target']} change",
                        "dimension": "semantic",
                    }

        return impacted

    def _transitive_callers(self, project_id: str, root_function: str) -> Set[str]:
        """Collect all transitive callers via BFS."""
        visited: Set[str] = {root_function}
        queue = deque([root_function])
        while queue:
            current = queue.popleft()
            fn = self.store.load_function(project_id, current)
            if fn is None:
                continue
            for caller in fn.callers:
                if caller not in visited:
                    visited.add(caller)
                    queue.append(caller)
        return visited

    def _data_flow_neighbors(self, project_id: str, root: FunctionRecord) -> Dict[str, str]:
        """Find functions that likely share data structures with root."""
        neighbors: Dict[str, str] = {}
        all_funcs = self.store.list_functions(project_id)

        # Same file functions that manipulate data
        for func in all_funcs:
            if func.file_path == root.file_path and func.function_path != root.function_path:
                if any(kw in func.function_path.lower() for kw in ["set_", "get_", "update_", "delete_", "create_"]):
                    neighbors[func.function_path] = "Same file data manipulation"

        return neighbors

    def _config_coupling(self, project_id: str, root: FunctionRecord) -> Dict[str, str]:
        """Find functions that might share configuration dependencies."""
        coupled: Dict[str, str] = {}
        all_funcs = self.store.list_functions(project_id)

        # Functions with config-like names
        config_keywords = ["config", "setting", "env", "constant", "init"]
        for func in all_funcs:
            if any(kw in func.function_path.lower() for kw in config_keywords):
                if func.feature_name == root.feature_name:
                    coupled[func.function_path] = "Shared feature configuration"

        return coupled

    # ------------------------------------------------------------------
    # Impact edges with confidence and evidence
    # ------------------------------------------------------------------
    def _build_edges(
        self,
        project_id: str,
        root: FunctionRecord,
        impacted: Dict[str, dict],
        intent: dict,
    ) -> List[ImpactEdge]:
        """Build impact edges with confidence scoring."""
        edges = []

        for target, meta in impacted.items():
            if target == root.function_path:
                continue

            confidence = meta["confidence"]
            reason = meta["reason"]
            dimension = meta["dimension"]

            # Adjust confidence based on intent
            if intent["target"] == "schema" and dimension == "data_flow":
                confidence = min(1.0, confidence + 0.2)
                reason += " | Schema changes strongly affect data flow"
            elif intent["target"] == "api" and dimension == "call_graph":
                confidence = min(1.0, confidence + 0.15)
                reason += " | API changes propagate through call graph"

            edges.append(ImpactEdge(
                source=root.function_path,
                target=target,
                confidence=round(confidence, 2),
                reason=reason,
                dimension=dimension,
            ))

        return sorted(edges, key=lambda e: e.confidence, reverse=True)

    # ------------------------------------------------------------------
    # Risk grouping
    # ------------------------------------------------------------------
    def _group_by_risk(
        self,
        project_id: str,
        impacted: Dict[str, dict],
        edges: List[ImpactEdge],
        intent: dict,
    ) -> List[RiskGroup]:
        """Group impacted functions by risk level."""
        high, medium, low = [], [], []
        high_features, medium_features, low_features = set(), set(), set()

        all_functions = self.store.load_functions_batch(
            project_id, list(impacted.keys())
        ) if impacted else []

        # Build lookup
        func_lookup = {fn.function_path: fn for fn in all_functions}

        for edge in edges:
            fn = func_lookup.get(edge.target)
            if fn is None:
                continue

            # Risk scoring
            risk_score = edge.confidence

            # Boost risk for certain patterns
            if intent.get("action") in {"remove", "rename"}:
                risk_score += 0.2
            if "api" in edge.target.lower() or "endpoint" in edge.target.lower():
                risk_score += 0.15
            if len(fn.callers) > 5:
                risk_score += 0.1

            risk_score = min(1.0, risk_score)

            if risk_score >= 0.7:
                high.append(edge.target)
                high_features.add(fn.feature_name)
            elif risk_score >= 0.4:
                medium.append(edge.target)
                medium_features.add(fn.feature_name)
            else:
                low.append(edge.target)
                low_features.add(fn.feature_name)

        groups = []
        if high:
            groups.append(RiskGroup(
                risk="high",
                functions=sorted(high),
                features=sorted(high_features),
                evidence="High confidence direct dependencies or API exposure",
            ))
        if medium:
            groups.append(RiskGroup(
                risk="medium",
                functions=sorted(medium),
                features=sorted(medium_features),
                evidence="Indirect dependencies or data flow coupling",
            ))
        if low:
            groups.append(RiskGroup(
                risk="low",
                functions=sorted(low),
                features=sorted(low_features),
                evidence="Potential coupling via shared feature or semantic similarity",
            ))

        return groups

    # ------------------------------------------------------------------
    # Breakpoint detection
    # ------------------------------------------------------------------
    def _detect_breakpoints(
        self,
        root: FunctionRecord,
        impacted: Dict[str, dict],
        intent: dict,
    ) -> List[Breakpoint]:
        """Detect probable break points for signature/schema/API changes."""
        breakpoints = []

        # Signature breakpoints
        if intent["target"] in {"method", "class", "field"}:
            breakpoints.append(Breakpoint(
                kind="signature",
                function_path=root.function_path,
                description=f"Changing {intent['target']} signature breaks all callers",
                confidence=0.95,
            ))

        # DTO/Schema breakpoints
        if intent["target"] in {"schema", "field", "dto"}:
            for target in impacted:
                if any(kw in target.lower() for kw in ["serialize", "deserialize", "parse", "validate", "dto", "model"]):
                    breakpoints.append(Breakpoint(
                        kind="dto_schema",
                        function_path=target,
                        description="Likely references changed schema",
                        confidence=0.8,
                    ))

        # API contract breakpoints
        if intent["target"] == "api":
            for target in impacted:
                if any(kw in target.lower() for kw in ["handler", "controller", "route", "endpoint"]):
                    breakpoints.append(Breakpoint(
                        kind="api_contract",
                        function_path=target,
                        description="API endpoint affected by contract change",
                        confidence=0.85,
                    ))

        return breakpoints

    # ------------------------------------------------------------------
    # Test suggestions
    # ------------------------------------------------------------------
    def _suggest_tests(self, project_id: str, impacted: Dict[str, dict]) -> List[TestSuggestion]:
        """Suggest tests to run for impacted functions."""
        suggestions = []
        project_path = None

        # Try to find project root from first function
        for func_path in impacted:
            fn = self.store.load_function(project_id, func_path)
            if fn:
                project_path = Path(fn.file_path).parent
                break

        for func_path in impacted:
            fn = self.store.load_function(project_id, func_path)
            if fn is None:
                continue

            # Look for corresponding test file
            test_path = self._find_test_for_function(fn, project_path)
            confidence = 0.7 if test_path else 0.3

            suggestions.append(TestSuggestion(
                function_path=func_path,
                test_path=test_path or "",
                confidence=confidence,
                reason="Direct caller of changed function" if confidence > 0.5 else "No corresponding test found",
            ))

        return sorted(suggestions, key=lambda t: t.confidence, reverse=True)

    def _find_test_for_function(self, fn: FunctionRecord, project_path: Optional[Path]) -> str:
        """Find corresponding test file for a function."""
        if project_path is None:
            return ""

        file_name = Path(fn.file_path).name
        test_patterns = [
            f"test_{file_name}",
            f"{file_name.replace('.py', '')}_test.py",
            f"tests/test_{file_name}",
            f"tests/{file_name.replace('.py', '')}_test.py",
        ]

        for pattern in test_patterns:
            test_file = project_path / pattern
            if test_file.exists():
                return str(test_file)

        return ""

    # ------------------------------------------------------------------
    # Coverage gaps
    # ------------------------------------------------------------------
    def _find_coverage_gaps(
        self, project_id: str, impacted: Dict[str, dict]
    ) -> List[CoverageGap]:
        """Identify impacted functions without corresponding tests."""
        gaps = []
        project_path = None

        for func_path in impacted:
            fn = self.store.load_function(project_id, func_path)
            if fn and project_path is None:
                project_path = Path(fn.file_path).parent

        for func_path in impacted:
            fn = self.store.load_function(project_id, func_path)
            if fn is None:
                continue

            test_path = self._find_test_for_function(fn, project_path)
            if not test_path:
                gaps.append(CoverageGap(
                    function_path=func_path,
                    feature_name=fn.feature_name,
                    reason="No corresponding test file found",
                ))

        return gaps

    # ------------------------------------------------------------------
    # Hotspot detection
    # ------------------------------------------------------------------
    def detect_hotspots(self, project_id: str) -> HotspotReport:
        """Detect architectural hotspots using centrality metrics."""
        all_funcs = self.store.list_functions(project_id)
        if not all_funcs:
            return HotspotReport(project_id=project_id)

        # Calculate fan-in and fan-out
        fan_in = {fn.function_path: len(fn.callers) for fn in all_funcs}
        fan_out = {fn.function_path: len(fn.callees) for fn in all_funcs}

        # Calculate betweenness centrality (approximation)
        centrality = self._approximate_betweenness(project_id, all_funcs)

        # Calculate instability: I = Ce / (Ca + Ce)
        # Ce = afferent coupling (fan-out), Ca = efferent coupling (fan-in)
        hotspots = []
        for fn in all_funcs:
            ca = fan_in.get(fn.function_path, 0)
            ce = fan_out.get(fn.function_path, 0)
            instability = ce / (ca + ce) if (ca + ce) > 0 else 0.0

            score = centrality.get(fn.function_path, 0)
            if score > 0.1 or ca > 5 or ce > 5:
                reason = []
                if score > 0.1:
                    reason.append(f"high centrality ({score:.2f})")
                if ca > 5:
                    reason.append(f"high fan-in ({ca})")
                if ce > 5:
                    reason.append(f"high fan-out ({ce})")
                if instability > 0.7:
                    reason.append(f"unstable ({instability:.2f})")

                hotspots.append(Hotspot(
                    function_path=fn.function_path,
                    feature_name=fn.feature_name,
                    centrality_score=round(score, 3),
                    fan_in=ca,
                    fan_out=ce,
                    instability=round(instability, 2),
                    reason="; ".join(reason),
                ))

        # Find unstable clusters
        clusters = self._find_unstable_clusters(all_funcs, fan_in, fan_out)

        hotspots.sort(key=lambda h: h.centrality_score, reverse=True)
        return HotspotReport(
            project_id=project_id,
            hotspots=hotspots[:20],  # Top 20
            unstable_clusters=clusters,
        )

    def _approximate_betweenness(
        self, project_id: str, all_funcs: List[FunctionRecord]
    ) -> Dict[str, float]:
        """Approximate betweenness centrality using random walks."""
        centrality: Dict[str, float] = defaultdict(float)
        func_paths = [fn.function_path for fn in all_funcs]

        # Sample from nodes with high connectivity
        sample_size = min(20, len(func_paths))
        import random
        samples = random.sample(func_paths, sample_size) if len(func_paths) > sample_size else func_paths

        for source in samples:
            # BFS to count shortest paths
            visited = {source: 1}
            queue = deque([source])
            while queue:
                current = queue.popleft()
                fn = self.store.load_function(project_id, current)
                if fn is None:
                    continue
                for callee in fn.callees:
                    if callee not in visited:
                        visited[callee] = visited[current] + 1
                        queue.append(callee)

            # Update centrality
            for node, distance in visited.items():
                if distance > 1:
                    centrality[node] += 1.0 / distance

        # Normalize
        max_score = max(centrality.values()) if centrality else 1.0
        if max_score > 0:
            for node in centrality:
                centrality[node] /= max_score

        return dict(centrality)

    def _find_unstable_clusters(
        self,
        all_funcs: List[FunctionRecord],
        fan_in: Dict[str, int],
        fan_out: Dict[str, int],
    ) -> List[List[str]]:
        """Find clusters of mutually dependent functions."""
        clusters = []
        visited = set()

        for fn in all_funcs:
            if fn.function_path in visited:
                continue

            ca = fan_in.get(fn.function_path, 0)
            ce = fan_out.get(fn.function_path, 0)
            if ca > 3 and ce > 3:
                # This node is part of a potential cluster
                cluster = [fn.function_path]
                visited.add(fn.function_path)

                # Find mutual dependencies
                for callee in fn.callees:
                    callee_fn = next((f for f in all_funcs if f.function_path == callee), None)
                    if callee_fn and fn.function_path in callee_fn.callers:
                        cluster.append(callee)
                        visited.add(callee)

                if len(cluster) > 1:
                    clusters.append(sorted(cluster))

        return clusters

    # ------------------------------------------------------------------
    # Diff-aware incremental analysis
    # ------------------------------------------------------------------
    def analyze_diff(
        self,
        project_id: str,
        repo_path: str,
        diff_source: str = "git",
    ) -> DiffImpactReport:
        """Analyze current git diff and rank impact in real-time."""
        changes = self._load_diff(repo_path, diff_source)
        if not changes:
            return DiffImpactReport(
                project_id=project_id,
                changes=[],
                impacted_functions=[],
                impacted_features=[],
                risk_level="low",
                suggestions=["No changes detected."],
            )

        # Map changed files to functions
        impacted_funcs = set()
        for change in changes:
            funcs = self._find_functions_in_file(project_id, change.file_path)
            impacted_funcs.update(funcs)

        # Re-rank impact
        impacted_features = sorted({
            self.store.load_function(project_id, fp).feature_name
            for fp in impacted_funcs
            if self.store.load_function(project_id, fp) is not None
        })

        # Risk based on change size
        total_lines = sum(c.lines_added + c.lines_deleted for c in changes)
        risk = "low"
        if total_lines > 100:
            risk = "high"
        elif total_lines > 30:
            risk = "medium"

        suggestions = [
            f"Detected {len(changes)} changed files affecting {len(impacted_funcs)} functions.",
            "Run tests for impacted features before committing.",
        ]
        if risk == "high":
            suggestions.append("Large change detected. Consider breaking into smaller commits.")

        return DiffImpactReport(
            project_id=project_id,
            changes=changes,
            impacted_functions=sorted(impacted_funcs),
            impacted_features=impacted_features,
            risk_level=risk,
            suggestions=suggestions,
        )

    def _load_diff(self, repo_path: str, source: str) -> List[DiffChange]:
        """Load diff from git or provided patch."""
        if source == "git":
            return self._load_git_diff(repo_path)
        return []

    def _load_git_diff(self, repo_path: str) -> List[DiffChange]:
        """Execute git diff and parse changes."""
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "diff", "--stat"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return []

            changes = []
            for line in result.stdout.strip().split("\n"):
                # Parse lines like: " file.py | 5 +++--"
                match = re.match(r"^\s*(\S+)\s*\|\s*(\d+)\s*([+-]*)$", line)
                if match:
                    file_path = match.group(1)
                    lines_changed = int(match.group(2))
                    changes.append(DiffChange(
                        file_path=file_path,
                        change_type="modified",
                        lines_added=lines_changed,
                        lines_deleted=0,
                    ))

            return changes
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def _find_functions_in_file(self, project_id: str, file_path: str) -> Set[str]:
        """Find all functions in a given file using indexed lookup."""
        funcs = self.store.get_functions_by_file_path(project_id, file_path)
        return {fn.function_path for fn in funcs}

    # ------------------------------------------------------------------
    # Ownership and boundary overlays
    # ------------------------------------------------------------------
    def get_boundary_map(self, project_id: str) -> List[BoundaryMap]:
        """Map features to module boundaries and detect violations."""
        features = self.store.list_features(project_id)
        boundaries = []

        for feature in features:
            # Derive owner from directory structure
            owner = self._derive_owner(feature.files)

            # Find boundary violations (dependencies on unrelated modules)
            violations = []
            for dep in feature.dependencies:
                dep_feature = next((f for f in features if f.feature_name == dep), None)
                if dep_feature:
                    dep_owner = self._derive_owner(dep_feature.files)
                    if owner and dep_owner and owner != dep_owner:
                        violations.append(
                            f"{feature.feature_name} depends on {dep} (cross-module: {owner} -> {dep_owner})"
                        )

            boundaries.append(BoundaryMap(
                feature_name=feature.feature_name,
                owner=owner,
                files=feature.files,
                boundary_violations=violations,
            ))

        return boundaries

    def _derive_owner(self, files: List[str]) -> str:
        """Derive module owner from file paths."""
        if not files:
            return ""

        # Common owner from directory structure
        paths = [Path(f) for f in files]
        if not paths:
            return ""

        # Find common parent directory
        common = paths[0].parent
        for p in paths[1:]:
            while not str(p).startswith(str(common)):
                common = common.parent
                if common == common.parent:
                    break

        return common.name if common != common.parent else ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _overall_risk(self, risk_groups: List[RiskGroup]) -> str:
        """Determine overall risk from groups."""
        if any(g.risk == "high" for g in risk_groups):
            return "high"
        if any(g.risk == "medium" for g in risk_groups):
            return "medium"
        return "low"

    def _generate_suggestions(
        self,
        intent: dict,
        risk_groups: List[RiskGroup],
        breakpoints: List[Breakpoint],
    ) -> List[str]:
        """Generate contextual suggestions."""
        suggestions = []

        high_count = sum(len(g.functions) for g in risk_groups if g.risk == "high")
        if high_count > 0:
            suggestions.append(f"{high_count} high-risk functions identified. Review carefully.")

        if intent["action"] == "remove":
            suggestions.append("Removing code? Ensure all callers are migrated first.")
        elif intent["action"] == "rename":
            suggestions.append("Renaming? Update all references and consider deprecation cycle.")
        elif intent["action"] == "split":
            suggestions.append("Splitting? Maintain backward compatibility during transition.")

        if any(b.kind == "signature" for b in breakpoints):
            suggestions.append("Signature change detected. Update all callers and type annotations.")

        if any(b.kind == "api_contract" for b in breakpoints):
            suggestions.append("API contract change. Update documentation and notify consumers.")

        suggestions.extend([
            "Run focused tests for all impacted features before merge.",
            "Verify call contract and argument compatibility for upstream callers.",
        ])

        return suggestions
