from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class FunctionRecord(BaseModel):
    function_path: str
    file_path: str
    feature_name: str
    start_line: int
    end_line: int
    docstring: str = ""
    source: str = ""
    callers: List[str] = Field(default_factory=list)
    callees: List[str] = Field(default_factory=list)


class FeatureRecord(BaseModel):
    feature_name: str
    functions: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    intent: str = ""  # Developer-intended name/purpose
    files: List[str] = Field(default_factory=list)  # Source files


class GraphIndex(BaseModel):
    project_id: str
    updated_at: str
    total_features: int
    total_functions: int
    features: Dict[str, str] = Field(default_factory=dict)
    functions: Dict[str, str] = Field(default_factory=dict)


class SyncResult(BaseModel):
    project_id: str
    indexed_features: int
    indexed_functions: int
    updated_at: str
    incremental: bool
    config_path: str


class FeatureContext(BaseModel):
    project_id: str
    feature_name: str
    functions: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    downstream_features: List[str] = Field(default_factory=list)


class ImpactEdge(BaseModel):
    """A single impact edge with confidence and evidence."""
    source: str
    target: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    dimension: Literal["call_graph", "data_flow", "semantic", "signature", "config"] = "call_graph"


class RiskGroup(BaseModel):
    """Functions grouped by risk level with evidence."""
    risk: Literal["high", "medium", "low"]
    functions: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)
    evidence: str = ""


class TestSuggestion(BaseModel):
    """Suggested test for an impacted area."""
    function_path: str
    test_path: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class CoverageGap(BaseModel):
    """Area that lacks test coverage."""
    function_path: str
    feature_name: str = ""
    reason: str = ""


class Breakpoint(BaseModel):
    """Probable break point for a change type."""
    kind: Literal["signature", "dto_schema", "api_contract", "data_flow", "config"]
    function_path: str
    description: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class ImpactReport(BaseModel):
    project_id: str
    root_function: str
    change_summary: str = ""
    change_intent: str = ""
    impacted_features: List[str] = Field(default_factory=list)
    impacted_functions: List[str] = Field(default_factory=list)
    risk_groups: List[RiskGroup] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    edges: List[ImpactEdge] = Field(default_factory=list)
    breakpoints: List[Breakpoint] = Field(default_factory=list)
    test_suggestions: List[TestSuggestion] = Field(default_factory=list)
    coverage_gaps: List[CoverageGap] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    confidence: float = 0.0


class PathTrace(BaseModel):
    project_id: str
    from_feature: str
    to_feature: str
    path: List[str] = Field(default_factory=list)
    found: bool = False


class SearchHit(BaseModel):
    kind: Literal["feature", "function"]
    name: str
    score: float
    details: str = ""


class SearchResult(BaseModel):
    project_id: str
    query: str
    search_type: str
    results: List[SearchHit] = Field(default_factory=list)


class FeatureList(BaseModel):
    project_id: str
    features: List[str] = Field(default_factory=list)
    stats: Optional[Dict[str, int]] = None


class FunctionDetail(BaseModel):
    project_id: str
    function: FunctionRecord
    callers: List[FunctionRecord] = Field(default_factory=list)
    callees: List[FunctionRecord] = Field(default_factory=list)


class Hotspot(BaseModel):
    """Architectural hotspot with centrality metrics."""
    function_path: str
    feature_name: str = ""
    centrality_score: float = 0.0
    fan_in: int = 0
    fan_out: int = 0
    instability: float = 0.0  # 0 = stable, 1 = unstable
    reason: str = ""


class HotspotReport(BaseModel):
    project_id: str
    hotspots: List[Hotspot] = Field(default_factory=list)
    unstable_clusters: List[List[str]] = Field(default_factory=list)


class DiffChange(BaseModel):
    """A single change from git diff."""
    file_path: str
    function_path: str = ""
    change_type: Literal["added", "modified", "deleted"] = "modified"
    lines_added: int = 0
    lines_deleted: int = 0


class DiffImpactReport(BaseModel):
    project_id: str
    changes: List[DiffChange] = Field(default_factory=list)
    impacted_functions: List[str] = Field(default_factory=list)
    impacted_features: List[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    suggestions: List[str] = Field(default_factory=list)


class BoundaryMap(BaseModel):
    """Module boundary with ownership info."""
    feature_name: str
    owner: str = ""  # Directory or team
    files: List[str] = Field(default_factory=list)
    boundary_violations: List[str] = Field(default_factory=list)


class ExtractedFunction(BaseModel):
    function_path: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str = ""
    source: str = ""
    raw_calls: List[str] = Field(default_factory=list)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")