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


class ImpactReport(BaseModel):
    project_id: str
    root_function: str
    change_summary: str = ""
    impacted_features: List[str] = Field(default_factory=list)
    impacted_functions: List[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    suggestions: List[str] = Field(default_factory=list)


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