from __future__ import annotations

import pickle
from pathlib import Path
from typing import List, Optional


class ASTCache:
    """Cache parsed ASTs to disk for fast incremental syncs."""
    
    def __init__(self, cache_dir: str = ".trellis/ast_cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _cache_path(self, file_hash: str) -> Path:
        """Get cache file path for a file hash."""
        # Use first 2 chars as subdirectory to avoid too many files in one dir
        return self.cache_dir / file_hash[:2] / f"{file_hash}.pickle"
    
    def get(self, file_path: str, file_hash: str) -> Optional[List[dict]]:
        """Get cached AST for a file if hash matches."""
        cache_path = self._cache_path(file_hash)
        if not cache_path.exists():
            return None
        
        try:
            with open(cache_path, "rb") as f:
                cached = pickle.load(f)
            # Validate the cached data is for this file
            if cached.get("file_path") == file_path:
                return cached.get("functions")
        except (pickle.PickleError, IOError):
            pass
        
        return None
    
    def set(self, file_path: str, file_hash: str, functions: List[dict]) -> None:
        """Cache AST for a file."""
        cache_path = self._cache_path(file_hash)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(cache_path, "wb") as f:
                pickle.dump({"file_path": file_path, "functions": functions}, f)
        except (pickle.PickleError, IOError):
            pass
    
    def clear(self) -> None:
        """Clear all cached ASTs."""
        import shutil
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def clean_old(self, max_age_days: int = 30) -> int:
        """Remove cached ASTs older than max_age_days. Returns count removed."""
        from datetime import datetime, timedelta
        
        cutoff = datetime.now() - timedelta(days=max_age_days)
        cutoff_timestamp = cutoff.timestamp()
        removed = 0
        
        for cache_file in self.cache_dir.rglob("*.pickle"):
            try:
                if cache_file.stat().st_mtime < cutoff_timestamp:
                    cache_file.unlink()
                    removed += 1
            except OSError:
                pass
        
        return removed
