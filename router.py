from __future__ import annotations

from typing import Dict, Optional

from models import GraphIndex
from store import GraphStore


class FeatureRouter:
    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self._index_cache: Dict[str, GraphIndex] = {}

    def refresh(self, project_id: str) -> Optional[GraphIndex]:
        index = self.store.load_index(project_id)
        if index is not None:
            self._index_cache[project_id] = index
        return index

    def get_index(self, project_id: str) -> Optional[GraphIndex]:
        if project_id in self._index_cache:
            return self._index_cache[project_id]
        return self.refresh(project_id)

    def list_feature_names(self, project_id: str) -> list[str]:
        index = self.get_index(project_id)
        if not index:
            return []
        return sorted(index.features.keys())

    def project_count(self) -> int:
        return len(self._index_cache)