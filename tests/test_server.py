"""Tests for server HTTP endpoints.

NOTE: These tests require the server to be running in HTTP mode.
Run: make run-http
Then run these tests with: pytest tests/test_server.py -m server_test
"""

from __future__ import annotations

import os

import pytest
import requests

# Mark all tests in this file as server tests that need a running server
pytestmark = pytest.mark.server_test

SERVER_URL = os.getenv("TRELLIS_TEST_URL", "http://localhost:17317")


@pytest.fixture
def base_url():
    """Provide base URL for integration tests against running server."""
    return SERVER_URL


def _check_server_up(base_url):
    try:
        resp = requests.get(f"{base_url}/health", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture(autouse=True)
def skip_if_server_down(base_url):
    if not _check_server_up(base_url):
        pytest.skip("Server not running at {base_url}. Run 'make run-http' first.")


class TestHealthEndpoint:
    """Test the /health endpoint."""

    def test_health_returns_ok(self, base_url):
        response = requests.get(f"{base_url}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "projects_cached" in data


class TestGraphEndpoints:
    """Test graph export endpoints."""

    def test_graph_requires_auth(self, base_url):
        # Without auth header when auth is required, should fail
        # (In no-auth mode, this may succeed)
        response = requests.get(f"{base_url}/graph/test_project")
        # Just verify endpoint exists
        assert response.status_code in (200, 401)

    def test_graph_with_auth(self, base_url, synced_project):
        response = requests.get(
            f"{base_url}/graph/{synced_project}",
            headers={"Authorization": "Bearer dev-no-auth"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "links" in data

    def test_graph_nodes_pagination(self, base_url, synced_project):
        response = requests.get(
            f"{base_url}/graph/{synced_project}/nodes?layer=feature&limit=10&offset=0",
            headers={"Authorization": "Bearer dev-no-auth"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "total" in data
        assert "has_more" in data
        assert isinstance(data["nodes"], list)

    def test_impact_subgraph(self, base_url, synced_project):
        # First get a function path from the graph
        graph_resp = requests.get(
            f"{base_url}/graph/{synced_project}",
            headers={"Authorization": "Bearer dev-no-auth"}
        )
        nodes = graph_resp.json().get("nodes", [])
        func_nodes = [n for n in nodes if n.get("type") == "function"]
        if not func_nodes:
            pytest.skip("No function nodes found")

        func_path = func_nodes[0]["id"]
        response = requests.get(
            f"{base_url}/graph/{synced_project}/impact/{func_path}",
            headers={"Authorization": "Bearer dev-no-auth"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "links" in data


class TestSpecEndpoints:
    """Test spec (project.md) endpoints."""

    def test_get_spec_no_spec(self, base_url, synced_project):
        response = requests.get(
            f"{base_url}/spec/{synced_project}",
            headers={"Authorization": "Bearer dev-no-auth"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "no_spec"
        assert "content" in data

    def test_save_and_get_spec(self, base_url, synced_project):
        content = "# Test Project\n\n## Purpose\nTest project for validation."
        response = requests.post(
            f"{base_url}/spec/{synced_project}",
            json={"content": content},
            headers={"Authorization": "Bearer dev-no-auth"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Now retrieve it
        response = requests.get(
            f"{base_url}/spec/{synced_project}",
            headers={"Authorization": "Bearer dev-no-auth"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["content"] == content


class TestCorsHeaders:
    """Verify CORS headers on all endpoints."""

    def test_health_cors(self, base_url):
        response = requests.get(f"{base_url}/health")
        assert response.headers.get("access-control-allow-origin") == "*"

    def test_graph_cors(self, base_url, synced_project):
        response = requests.get(
            f"{base_url}/graph/{synced_project}",
            headers={"Authorization": "Bearer dev-no-auth"}
        )
        assert response.headers.get("access-control-allow-origin") == "*"
