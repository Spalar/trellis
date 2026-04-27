from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from models import FeatureRecord, FunctionRecord, GraphIndex


SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class GraphStore:
    def __init__(self, base_dir: str = ".trellis/data") -> None:
        configured = os.getenv("TRELLIS_DATA_DIR", "").strip()
        self.base_dir = Path(configured or base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _safe_name(self, name: str) -> str:
        return SAFE_NAME_RE.sub("_", name).strip("_") or "default"

    def _project_dir(self, project_id: str) -> Path:
        project_dir = self.base_dir / self._safe_name(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "features").mkdir(exist_ok=True)
        (project_dir / "functions").mkdir(exist_ok=True)
        (project_dir / "snapshots").mkdir(exist_ok=True)
        return project_dir

    def _write_json(self, path: Path, payload: Dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=True)

    def _read_json(self, path: Path) -> Optional[Dict]:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def clear_project(self, project_id: str) -> None:
        project_dir = self._project_dir(project_id)
        for directory in ("features", "functions"):
            for item in (project_dir / directory).glob("*.json"):
                item.unlink(missing_ok=True)

    def save_index(self, project_id: str, index: GraphIndex) -> None:
        project_dir = self._project_dir(project_id)
        self._write_json(project_dir / "index.json", index.model_dump())

    def load_index(self, project_id: str) -> Optional[GraphIndex]:
        project_dir = self._project_dir(project_id)
        payload = self._read_json(project_dir / "index.json")
        return GraphIndex.model_validate(payload) if payload else None

    def save_feature(self, project_id: str, feature: FeatureRecord) -> str:
        project_dir = self._project_dir(project_id)
        safe = self._safe_name(feature.feature_name)
        relative = f"features/{safe}.json"
        self._write_json(project_dir / relative, feature.model_dump())
        return relative

    def load_feature(self, project_id: str, feature_name: str) -> Optional[FeatureRecord]:
        project_dir = self._project_dir(project_id)
        safe = self._safe_name(feature_name)
        payload = self._read_json(project_dir / "features" / f"{safe}.json")
        return FeatureRecord.model_validate(payload) if payload else None

    def list_features(self, project_id: str) -> List[FeatureRecord]:
        project_dir = self._project_dir(project_id)
        features: List[FeatureRecord] = []
        for file in sorted((project_dir / "features").glob("*.json")):
            payload = self._read_json(file)
            if payload:
                features.append(FeatureRecord.model_validate(payload))
        return features

    def save_function(self, project_id: str, function: FunctionRecord) -> str:
        project_dir = self._project_dir(project_id)
        safe = self._safe_name(function.function_path)
        relative = f"functions/{safe}.json"
        self._write_json(project_dir / relative, function.model_dump())
        return relative

    def load_function(self, project_id: str, function_path: str) -> Optional[FunctionRecord]:
        project_dir = self._project_dir(project_id)
        safe = self._safe_name(function_path)
        payload = self._read_json(project_dir / "functions" / f"{safe}.json")
        return FunctionRecord.model_validate(payload) if payload else None

    def load_functions_batch(self, project_id: str, function_paths: List[str]) -> List[FunctionRecord]:
        """Load multiple functions efficiently in one operation."""
        project_dir = self._project_dir(project_id)
        functions = []
        for path in function_paths:
            safe = self._safe_name(path)
            payload = self._read_json(project_dir / "functions" / f"{safe}.json")
            if payload:
                functions.append(FunctionRecord.model_validate(payload))
        return functions

    def list_functions(self, project_id: str) -> List[FunctionRecord]:
        project_dir = self._project_dir(project_id)
        functions: List[FunctionRecord] = []
        for file in sorted((project_dir / "functions").glob("*.json")):
            payload = self._read_json(file)
            if payload:
                functions.append(FunctionRecord.model_validate(payload))
        return functions

    def save_snapshot(self, project_id: str, payload: Dict) -> str:
        project_dir = self._project_dir(project_id)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        relative = f"snapshots/{stamp}.json"
        self._write_json(project_dir / relative, payload)
        return relative