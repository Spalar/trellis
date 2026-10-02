"""Synchronous HTTP client for the Trellis REST API (src/trellis/api.py).

Thin wrapper over the FastAPI backend: one method per endpoint, responses
passed through as decoded JSON, and non-2xx status codes raised as
``TrellisError`` with the server's ``{"error": ...}`` message.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

__all__ = ["TrellisClient", "TrellisError"]


class TrellisError(Exception):
    """A non-2xx response from the Trellis API."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class TrellisClient:
    """Synchronous client for the Trellis REST API.

    Sends ``Authorization: Bearer <api_key>`` on every request. When
    ``api_key`` is None the ``TRELLIS_API_KEY`` environment variable is read
    once at construction; when neither is set, requests are sent unauthenticated
    (only the public paths like ``/health`` will succeed on a token-guarded
    server).

    ``transport`` is passed through to ``httpx.Client``; it exists so tests can
    serve the app in-process instead of over a socket.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:17317",
        api_key: str | None = None,
        timeout: float = 30.0,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        if api_key is None:
            api_key = os.environ.get("TRELLIS_API_KEY")
        self.api_key = api_key
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying HTTP connection."""
        self._client.close()

    def __enter__(self) -> "TrellisClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        response = self._client.request(method, path, params=params, json=json)
        if response.status_code >= 400:
            message = response.text
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("error"), str):
                    message = body["error"]
            except ValueError:
                pass
            raise TrellisError(message, response.status_code)
        return response.json()

    def health(self) -> Any:
        """Health check (public, no token required)."""
        return self._request("GET", "/health")

    def list_projects(self) -> Any:
        """List registered projects with per-project sync summaries."""
        return self._request("GET", "/projects")

    def get_project(self, project_id: str) -> Any:
        """Full detail for one project (index, sync, spec, notes)."""
        return self._request("GET", f"/projects/{project_id}")

    def delete_project(self, project_id: str, delete_data: bool = False) -> Any:
        """Unregister a project; optionally also delete its trellis data dir.

        The repository itself is never touched. 404 when the project is
        unknown.
        """
        params = {"delete_data": delete_data} if delete_data else None
        return self._request("DELETE", f"/projects/{project_id}", params=params)

    def usage_stats(self) -> Any:
        """MCP tool-call telemetry (totals, per-tool counts, recent calls)."""
        return self._request("GET", "/stats/usage")

    def graph(self, project_id: str) -> Any:
        """Get graph data for a project."""
        return self._request("GET", f"/graph/{project_id}")

    def sync(self, project_id: str) -> Any:
        """Full index rebuild for a project (heavy; edits index automatically)."""
        return self._request("POST", f"/graph/{project_id}/sync")

    def sync_status(self, project_id: str) -> Any:
        """Index status including whether the file watcher is active."""
        return self._request("GET", f"/graph/{project_id}/status")

    def tour(self, project_id: str, path: str = "") -> Any:
        """Dependency-ordered reading tour, optionally scoped to a subtree."""
        params = {"path": path} if path else None
        return self._request("GET", f"/graph/{project_id}/tour", params=params)

    def project_health(self, project_id: str, limit: int = 0) -> Any:
        """Architecture-health snapshot (chokepoints, import cycles, edges)."""
        params = {"limit": limit} if limit else None
        return self._request("GET", f"/graph/{project_id}/health", params=params)

    def impact(self, project_id: str, symbol: str) -> Any:
        """Get the impact graph for a symbol."""
        return self._request("GET", f"/graph/{project_id}/impact/{symbol}")

    def feature_impact(self, project_id: str, symbol: str) -> Any:
        """Get the feature impact report for a symbol."""
        return self._request("GET", f"/feature/{project_id}/impact/{symbol}")

    def feature_pointers(self, project_id: str, symbol: str) -> Any:
        """Get development pointers for a symbol."""
        return self._request("GET", f"/feature/{project_id}/pointers/{symbol}")

    def feature_context(self, project_id: str, symbol: str) -> Any:
        """Get feature context for a symbol (404 when none exists)."""
        return self._request("GET", f"/feature/{project_id}/context/{symbol}")

    def feature_divergence(self, project_id: str, symbol: str) -> Any:
        """Check feature divergence for a symbol."""
        return self._request("GET", f"/feature/{project_id}/divergence/{symbol}")

    def get_spec(self, project_id: str) -> Any:
        """Return the project spec, or a template when none exists yet."""
        return self._request("GET", f"/spec/{project_id}")

    def save_spec(self, project_id: str, content: str) -> Any:
        """Save the project spec and refresh auto-generated feature notes."""
        return self._request(
            "POST", f"/spec/{project_id}", json={"content": content}
        )

    def spec_alignment(self, project_id: str) -> Any:
        """Verify project.md features against the synced code graph."""
        return self._request("GET", f"/spec/{project_id}/alignment")

    def knowledge_graph(self, project_id: str) -> Any:
        """Get knowledge graph data (notes + code)."""
        return self._request("GET", f"/knowledge-graph/{project_id}")

    def list_notes(self, project_id: str) -> Any:
        """List all knowledge notes for a project."""
        return self._request("GET", f"/notes/{project_id}")

    def get_note(self, project_id: str, note_id: str) -> Any:
        """Get a knowledge note (404 when it does not exist)."""
        return self._request("GET", f"/note/{project_id}/{note_id}")

    def create_note(
        self,
        project_id: str,
        note_id: str,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> Any:
        """Create or update a knowledge note.

        Omitted fields default server-side: title falls back to the note id,
        content to an empty string, tags to an empty list.
        """
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if content is not None:
            body["content"] = content
        if tags is not None:
            body["tags"] = tags
        return self._request("POST", f"/note/{project_id}/{note_id}", json=body)

    def delete_note(self, project_id: str, note_id: str) -> Any:
        """Delete a knowledge note (404 when it does not exist)."""
        return self._request("DELETE", f"/note/{project_id}/{note_id}")
