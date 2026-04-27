from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Set

from extractor import PythonTreeSitterExtractor
from feature_intent import FeatureIntentExtractor
from models import (
    FeatureContext,
    FeatureList,
    FeatureRecord,
    FunctionDetail,
    FunctionRecord,
    GraphIndex,
    ImpactReport,
    PathTrace,
    SearchHit,
    SearchResult,
    SyncResult,
    utc_now_iso,
)
from store import GraphStore


class TrellisEngine:
    def __init__(self, store: GraphStore, extractor: Optional[PythonTreeSitterExtractor] = None) -> None:
        self.store = store
        self.extractor = extractor or PythonTreeSitterExtractor()
        self.intent_extractor = FeatureIntentExtractor()

    def sync_project(
        self,
        project_id: str,
        repo_path: str,
        config_path: str,
        incremental: bool,
    ) -> SyncResult:
        if not incremental:
            self.store.clear_project(project_id)

        extraction_result = self.extractor.extract_repo(repo_path)
        functions_by_path = self._build_function_graph(extraction_result)
        features = self._build_feature_map(functions_by_path, extraction_result.file_contents)

        self._persist_project(project_id, functions_by_path, features)
        index = self.store.load_index(project_id)
        assert index is not None

        return SyncResult(
            project_id=project_id,
            indexed_features=index.total_features,
            indexed_functions=index.total_functions,
            updated_at=index.updated_at,
            incremental=incremental,
            config_path=config_path,
        )

    def _build_function_graph(self, extraction_result) -> Dict[str, FunctionRecord]:
        """Build the function call graph from extracted code."""
        extracted = extraction_result.functions
        all_paths = {item.function_path for item in extracted}
        file_intents = self._extract_file_intents_from_cache(extraction_result.file_contents)
        file_to_feature = self._map_files_to_features(file_intents)

        functions_by_path: Dict[str, FunctionRecord] = {}
        for item in extracted:
            resolved = self._resolve_calls(item.raw_calls, all_paths)
            feature_name = file_to_feature.get(item.file_path) or self._feature_from_path(item.function_path)

            fn = FunctionRecord(
                function_path=item.function_path,
                file_path=item.file_path,
                feature_name=feature_name,
                start_line=item.start_line,
                end_line=item.end_line,
                docstring=item.docstring,
                source=item.source,
                callers=[],
                callees=sorted(resolved),
            )
            functions_by_path[fn.function_path] = fn

        # Wire up callers
        for fn in functions_by_path.values():
            for callee in fn.callees:
                target = functions_by_path.get(callee)
                if target and fn.function_path not in target.callers:
                    target.callers.append(fn.function_path)

        for fn in functions_by_path.values():
            fn.callers.sort()

        return functions_by_path

    def _build_feature_map(
        self,
        functions_by_path: Dict[str, FunctionRecord],
        file_contents: Dict[str, str],
    ) -> Dict[str, FeatureRecord]:
        """Group functions into features and compute dependencies."""
        file_intents = self._extract_file_intents_from_cache(file_contents)
        feature_intents = self._get_feature_intents(file_intents)

        features: Dict[str, FeatureRecord] = {}
        feature_files: Dict[str, Set[str]] = defaultdict(set)

        for fn in functions_by_path.values():
            feature = features.setdefault(
                fn.feature_name,
                FeatureRecord(feature_name=fn.feature_name, functions=[], dependencies=[]),
            )
            feature.functions.append(fn.function_path)
            feature_files[fn.feature_name].add(fn.file_path)

        # Attach metadata
        for name, feature in features.items():
            feature.functions = sorted(set(feature.functions))
            feature.files = sorted(feature_files[name])
            intent = feature_intents.get(name, {})
            feature.intent = intent.get("primary_feature", "")

        # Compute cross-feature dependencies
        deps: Dict[str, Set[str]] = defaultdict(set)
        for fn in functions_by_path.values():
            for callee in fn.callees:
                callee_fn = functions_by_path.get(callee)
                if callee_fn and callee_fn.feature_name != fn.feature_name:
                    deps[fn.feature_name].add(callee_fn.feature_name)

        for name, feature in features.items():
            feature.dependencies = sorted(deps.get(name, set()))

        return features

    def _persist_project(
        self,
        project_id: str,
        functions_by_path: Dict[str, FunctionRecord],
        features: Dict[str, FeatureRecord],
    ) -> None:
        """Save graph to store and create index."""
        for feature in features.values():
            self.store.save_feature(project_id, feature)
        for fn in functions_by_path.values():
            self.store.save_function(project_id, fn)

        feature_map = dict(sorted({name: name for name in features}.items()))
        function_map = dict(sorted({name: name for name in functions_by_path}.items()))

        index = GraphIndex(
            project_id=project_id,
            updated_at=utc_now_iso(),
            total_features=len(feature_map),
            total_functions=len(function_map),
            features=feature_map,
            functions=function_map,
        )
        self.store.save_index(project_id, index)
        self.store.save_snapshot(
            project_id,
            {
                "index": index.model_dump(),
                "features": [f.model_dump() for f in features.values()],
                "functions": [fn.model_dump() for fn in functions_by_path.values()],
            },
        )

    def _extract_file_intents_from_cache(
        self,
        file_contents: Dict[str, str],
    ) -> Dict[str, dict]:
        """Extract developer-intended feature names from cached source contents."""
        intents = {}
        for file_path, source in file_contents.items():
            intent = self.intent_extractor.extract_from_file(file_path, source)
            intents[file_path] = intent
        return intents

    def _map_files_to_features(self, file_intents: Dict[str, dict]) -> Dict[str, str]:
        """Map each file to its intended feature name."""
        mapping = {}
        for file_path, intent in file_intents.items():
            primary = intent.get("primary_feature", "")
            if primary:
                mapping[file_path] = primary
        return mapping

    def _get_feature_intents(self, file_intents: Dict[str, dict]) -> Dict[str, dict]:
        """Aggregate file intents by feature name."""
        feature_intents: Dict[str, dict] = {}
        for file_path, intent in file_intents.items():
            primary = intent.get("primary_feature", "")
            if primary:
                if primary not in feature_intents:
                    feature_intents[primary] = intent
                else:
                    # Merge alternative names
                    existing = feature_intents[primary]
                    existing["alternative_names"] = list(set(
                        existing.get("alternative_names", []) + 
                        intent.get("alternative_names", [])
                    ))
        return feature_intents

    def get_feature(self, project_id: str, feature_name: str, include_dependencies: bool, depth: int) -> FeatureContext:
        feature = self.store.load_feature(project_id, feature_name)
        if feature is None:
            raise ValueError(f"Feature not found: {feature_name}")

        downstream: List[str] = []
        if include_dependencies:
            downstream = self._downstream_features(project_id, feature.feature_name, depth)

        return FeatureContext(
            project_id=project_id,
            feature_name=feature.feature_name,
            functions=feature.functions,
            dependencies=feature.dependencies,
            downstream_features=downstream,
        )

    def analyze_impact(
        self,
        project_id: str,
        function_path: str,
        change_summary: str,
        include_suggestions: bool,
    ) -> ImpactReport:
        resolved = self._resolve_function_path(project_id, function_path)
        if resolved is None:
            candidates = [
                f.function_path
                for f in self.store.list_functions(project_id)
            ][:10]
            raise ValueError(
                f"Function not found: {function_path}. "
                f"Try one of: {candidates}"
            )
        root = self.store.load_function(project_id, resolved)
        if root is None:
            raise ValueError(f"Function not found: {function_path}")

        impacted_functions = self._transitive_callers(project_id, root.function_path)
        # Batch load all impacted functions at once
        loaded_funcs = self.store.load_functions_batch(project_id, impacted_functions)

        # Multi-dimensional impact analysis
        impact_analysis = self._analyze_impact_multi_dimension(
            project_id, root, impacted_functions, loaded_funcs, change_summary
        )

        return ImpactReport(
            project_id=project_id,
            root_function=root.function_path,
            change_summary=change_summary,
            impacted_features=impact_analysis["features"],
            impacted_functions=impact_analysis["functions"],
            risk_level=impact_analysis["risk"],
            suggestions=impact_analysis["suggestions"],
        )

    def _analyze_impact_multi_dimension(
        self,
        project_id: str,
        root: FunctionRecord,
        impacted_functions: List[str],
        loaded_funcs: List[FunctionRecord],
        change_summary: str,
    ) -> dict:
        """Analyze impact across multiple dimensions for better accuracy."""
        
        # Dimension 1: Call graph (existing)
        call_graph_impact = set(impacted_functions)
        
        # Dimension 2: Data flow - functions that access same data structures
        data_flow_impact = self._analyze_data_flow_impact(project_id, root, loaded_funcs)
        
        # Dimension 3: Semantic impact based on change description
        semantic_impact = self._analyze_semantic_impact(change_summary, loaded_funcs)
        
        # Combine all dimensions
        all_impacted = call_graph_impact | data_flow_impact | semantic_impact
        
        # Get features
        impacted_features = sorted({fn.feature_name for fn in loaded_funcs 
                                   if fn.function_path in all_impacted})
        
        # Calculate risk with semantic weighting
        risk_level = self._calculate_risk(
            len(all_impacted), 
            len(impacted_features),
            change_summary
        )
        
        # Generate contextual suggestions
        suggestions = self._generate_suggestions(
            root, all_impacted, impacted_features, change_summary
        )
        
        return {
            "functions": sorted(all_impacted),
            "features": impacted_features,
            "risk": risk_level,
            "suggestions": suggestions,
        }

    def _analyze_data_flow_impact(
        self, 
        project_id: str, 
        root: FunctionRecord,
        all_functions: List[FunctionRecord]
    ) -> Set[str]:
        """Identify functions that might be impacted through shared data."""
        impacted = set()
        
        # Simple heuristic: functions in the same file that don't call each other
        # might share data structures
        same_file_funcs = [f for f in all_functions 
                          if f.file_path == root.file_path 
                          and f.function_path != root.function_path]
        
        for func in same_file_funcs:
            # Check if function name suggests data manipulation
            if any(keyword in func.function_path.lower() for keyword in 
                   ['set_', 'get_', 'update_', 'delete_', 'create_', 'modify_']):
                impacted.add(func.function_path)
        
        return impacted

    def _analyze_semantic_impact(
        self, 
        change_summary: str, 
        all_functions: List[FunctionRecord]
    ) -> Set[str]:
        """Use semantic analysis of change description to find likely impacts."""
        impacted = set()
        
        if not change_summary:
            return impacted
        
        # Keywords that suggest broad impact
        broad_impact_keywords = ['api', 'interface', 'contract', 'schema', 'protocol', 
                                'config', 'setting', 'global', 'shared', 'core']
        
        change_lower = change_summary.lower()
        is_broad_change = any(kw in change_lower for kw in broad_impact_keywords)
        
        if is_broad_change:
            # For broad changes, flag functions that might be affected
            for func in all_functions:
                # Flag entry points and public APIs
                if not func.function_path.startswith('_'):
                    impacted.add(func.function_path)
        
        return impacted

    def _calculate_risk(
        self, 
        impacted_count: int, 
        feature_count: int,
        change_summary: str
    ) -> str:
        """Calculate risk with semantic weighting."""
        # Base risk from count
        if impacted_count > 20 or feature_count > 5:
            base_risk = 3  # high
        elif impacted_count > 8 or feature_count > 2:
            base_risk = 2  # medium
        else:
            base_risk = 1  # low
        
        # Adjust based on change description
        change_lower = change_summary.lower()
        
        # Increase risk for certain types of changes
        risky_keywords = ['breaking', 'refactor', 'rewrite', 'remove', 'delete']
        if any(kw in change_lower for kw in risky_keywords):
            base_risk = min(3, base_risk + 1)
        
        # Decrease risk for minor changes
        safe_keywords = ['comment', 'docstring', 'log', 'print', 'rename']
        if any(kw in change_lower for kw in safe_keywords):
            base_risk = max(1, base_risk - 1)
        
        return {1: "low", 2: "medium", 3: "high"}[base_risk]

    def _generate_suggestions(
        self,
        root: FunctionRecord,
        impacted: Set[str],
        features: List[str],
        change_summary: str,
    ) -> List[str]:
        """Generate contextual suggestions based on impact analysis."""
        suggestions = [
            f"Function '{root.function_path}' affects {len(impacted)} functions across {len(features)} features.",
        ]
        
        if len(features) > 3:
            suggestions.append(
                "This change spans multiple features. Consider breaking it into smaller PRs."
            )
        
        if any(kw in change_summary.lower() for kw in ['api', 'interface']):
            suggestions.append(
                "API changes detected. Update API documentation and notify consumers."
            )
        
        suggestions.extend([
            "Run focused tests for all impacted features before merge.",
            "Verify call contract and argument compatibility for upstream callers.",
        ])
        
        return suggestions

    def analyze_feature_impact(
        self,
        project_id: str,
        feature_name: str,
        change_summary: str,
        include_suggestions: bool,
    ) -> ImpactReport:
        """Analyze impact at the feature level (all functions in the feature)."""
        feature = self.store.load_feature(project_id, feature_name)
        if feature is None:
            raise ValueError(f"Feature not found: {feature_name}")

        all_functions = self.store.list_functions(project_id)
        feat_functions = {f.function_path for f in all_functions if f.feature_name == feature_name}

        # Collect all upstream callers of any function in this feature
        visited: Set[str] = set()
        queue = deque(feat_functions)
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            fn = self.store.load_function(project_id, current)
            if fn is None:
                continue
            for caller in fn.callers:
                queue.append(caller)

        impacted_functions = sorted(visited - feat_functions)
        impacted_features = sorted(
            {
                self.store.load_function(project_id, fn).feature_name
                for fn in impacted_functions
                if self.store.load_function(project_id, fn) is not None
            }
        )

        risk_level = "low"
        size = len(impacted_functions)
        if size > 50:
            risk_level = "high"
        elif size > 15:
            risk_level = "medium"

        suggestions: List[str] = []
        if include_suggestions:
            suggestions = [
                f"Feature '{feature_name}' has {len(feat_functions)} functions.",
                "Run full test suite for all impacted features before merge.",
                "Verify interface contracts between affected modules.",
                "Update architecture docs and changelogs for changed behavior.",
            ]

        return ImpactReport(
            project_id=project_id,
            root_function=feature_name,
            change_summary=change_summary,
            impacted_features=impacted_features,
            impacted_functions=impacted_functions,
            risk_level=risk_level,
            suggestions=suggestions,
        )

    def trace_path(self, project_id: str, from_feature: str, to_feature: str, max_depth: int) -> PathTrace:
        features = self.store.list_features(project_id)
        adjacency = {feature.feature_name: feature.dependencies for feature in features}

        if from_feature not in adjacency or to_feature not in adjacency:
            return PathTrace(project_id=project_id, from_feature=from_feature, to_feature=to_feature, found=False, path=[])

        queue = deque([(from_feature, [from_feature])])
        visited = {from_feature}
        while queue:
            node, path = queue.popleft()
            if node == to_feature:
                return PathTrace(
                    project_id=project_id,
                    from_feature=from_feature,
                    to_feature=to_feature,
                    found=True,
                    path=path,
                )
            if len(path) > max_depth:
                continue
            for nxt in adjacency.get(node, []):
                if nxt in visited:
                    continue
                visited.add(nxt)
                queue.append((nxt, path + [nxt]))

        return PathTrace(project_id=project_id, from_feature=from_feature, to_feature=to_feature, found=False, path=[])

    def search(self, project_id: str, query: str, search_type: str, limit: int) -> SearchResult:
        q = query.lower().strip()
        hits: List[SearchHit] = []

        for feature in self.store.list_features(project_id):
            score = self._score(feature.feature_name.lower(), q)
            if score > 0:
                hits.append(
                    SearchHit(
                        kind="feature",
                        name=feature.feature_name,
                        score=score,
                        details=f"{len(feature.functions)} functions",
                    )
                )

        for fn in self.store.list_functions(project_id):
            searchable = f"{fn.function_path} {fn.docstring}".lower()
            score = self._score(searchable, q)
            if score > 0:
                hits.append(
                    SearchHit(
                        kind="function",
                        name=fn.function_path,
                        score=score,
                        details=f"{Path(fn.file_path).name}:{fn.start_line}",
                    )
                )

        hits.sort(key=lambda item: item.score, reverse=True)
        return SearchResult(
            project_id=project_id,
            query=query,
            search_type=search_type,
            results=hits[: max(1, limit)],
        )

    def list_features(self, project_id: str, include_stats: bool) -> FeatureList:
        features = self.store.list_features(project_id)
        names = sorted(item.feature_name for item in features)
        stats = None
        if include_stats:
            stats = {
                "total_features": len(features),
                "total_functions": sum(len(item.functions) for item in features),
            }
        return FeatureList(project_id=project_id, features=names, stats=stats)

    def get_function(
        self,
        project_id: str,
        function_path: str,
        include_callers: bool,
        include_callees: bool,
    ) -> FunctionDetail:
        resolved = self._resolve_function_path(project_id, function_path)
        if resolved is None:
            raise ValueError(f"Function not found: {function_path}")
        fn = self.store.load_function(project_id, resolved)
        if fn is None:
            raise ValueError(f"Function not found: {function_path}")

        callers = []
        callees = []
        if include_callers:
            callers = [
                loaded
                for loaded in (self.store.load_function(project_id, item) for item in fn.callers)
                if loaded is not None
            ]
        if include_callees:
            callees = [
                loaded
                for loaded in (self.store.load_function(project_id, item) for item in fn.callees)
                if loaded is not None
            ]

        return FunctionDetail(project_id=project_id, function=fn, callers=callers, callees=callees)

    def _resolve_function_path(self, project_id: str, function_path: str) -> Optional[str]:
        """Resolve a function path with fuzzy fallback."""
        # Exact match first
        if self.store.load_function(project_id, function_path) is not None:
            return function_path

        # If the user passed a bare method name like 'validate_auth',
        # look for any function whose final segment matches.
        all_functions = self.store.list_functions(project_id)
        if "." not in function_path and "/" not in function_path:
            for fn in all_functions:
                if fn.function_path.split(".")[-1] == function_path:
                    return fn.function_path
                if fn.function_path.split("/")[-1] == function_path:
                    return fn.function_path

        # Fuzzy: case-insensitive substring or suffix match
        lowered = function_path.lower()
        for fn in all_functions:
            if lowered in fn.function_path.lower():
                return fn.function_path
            if fn.function_path.lower().endswith(lowered):
                return fn.function_path
            # Handle JS/TS paths where user may drop the leading directory
            if lowered.replace("/", ".") in fn.function_path.lower().replace("/", "."):
                return fn.function_path

        # Last resort: try the user's exact input even after cleanup
        # (tree-sitter extracted values may differ slightly)
        for fn in all_functions:
            safe = fn.function_path.replace("__", "").replace("_", "").lower()
            query_safe = function_path.replace("__", "").replace("_", "").lower()
            if safe == query_safe:
                return fn.function_path

        return None

    def _feature_from_path(self, function_path: str) -> str:
        """Derive a feature name from a function path.

        Python paths look like ``engine.TrellisEngine.analyze_impact``.
        JS/TS paths look like ``apps/image-editor/src/js/action.activateIconMode``.
        """
        # JS / TS — the last ``/`` segment is usually the file / module name.
        if "/" in function_path:
            parts = function_path.split("/")
            last = parts[-1] if parts else ""
            # Strip any trailing dotted method name so we keep the module.
            last = last.split(".")[0]
            return last[:1].upper() + last[1:] if last else "Core"

        # Python — first dotted segment is the module.
        if "." in function_path:
            first = function_path.split(".")[0]
            return first[:1].upper() + first[1:] if first else "Core"

        return "Core"

    def _resolve_calls(self, raw_calls: List[str], available_paths: Set[str]) -> Set[str]:
        resolved: Set[str] = set()
        by_suffix = {path.split(".")[-1]: path for path in available_paths}
        for call in raw_calls:
            if call in available_paths:
                resolved.add(call)
                continue
            leaf = call.split(".")[-1]
            target = by_suffix.get(leaf)
            if target:
                resolved.add(target)
        return resolved

    def _downstream_features(self, project_id: str, feature_name: str, depth: int) -> List[str]:
        features = {item.feature_name: item for item in self.store.list_features(project_id)}
        visited: Set[str] = set()
        queue = deque([(feature_name, 0)])
        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for nxt in features.get(current, FeatureRecord(feature_name=current)).dependencies:
                if nxt in visited:
                    continue
                visited.add(nxt)
                queue.append((nxt, current_depth + 1))
        return sorted(visited)

    def _transitive_callers(self, project_id: str, root_function: str) -> List[str]:
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
        return sorted(visited)

    def _score(self, text: str, query: str) -> float:
        if not query:
            return 0.0
        if text == query:
            return 1.0
        if query in text:
            return min(0.95, len(query) / max(len(text), 1) + 0.4)
        terms = [term for term in query.split() if term]
        if not terms:
            return 0.0
        matched = sum(1 for term in terms if term in text)
        if matched == 0:
            return 0.0
        return matched / len(terms) * 0.5