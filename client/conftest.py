"""Test bootstrap for the trellis-client package.

Adds the repo root (for ``src.trellis``) and this directory (for
``trellis_client``) to sys.path so the tests import both the server app and
the SDK without an editable install, no matter the invocation directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
import httpx
import pytest

CLIENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CLIENT_DIR.parent

for path in (str(REPO_ROOT), str(CLIENT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from src.trellis import core  # noqa: E402
from src.trellis.api import create_api_app  # noqa: E402
from trellis_client import TrellisClient  # noqa: E402

TOKEN = "sdk-test-token"


class SyncASGITransport(httpx.BaseTransport):
    """Drive an ASGI app (e.g. the Trellis FastAPI app) from a sync httpx.Client.

    httpx's ``ASGITransport`` is async-only, so this adapter runs it on a
    blocking anyio portal and buffers request/response bodies.
    """

    def __init__(self, app):
        self._portal_manager = anyio.from_thread.start_blocking_portal()
        self._portal = self._portal_manager.__enter__()
        self._transport = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._portal.call(self._handle_async, request)

    async def _handle_async(self, request: httpx.Request) -> httpx.Response:
        chunks = list(request.stream)

        class _AsyncStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for chunk in chunks:
                    yield chunk

        async_request = httpx.Request(
            request.method,
            request.url,
            headers=request.headers,
            stream=_AsyncStream(),
        )
        response = await self._transport.handle_async_request(async_request)
        body = b"".join([chunk async for chunk in response.stream])
        return httpx.Response(
            response.status_code, headers=response.headers, content=body, request=request
        )

    def close(self) -> None:
        self._portal_manager.__exit__(None, None, None)


@pytest.fixture
def token(monkeypatch):
    """Pin the server guard token; core.http_token is fixed at import time."""
    monkeypatch.setattr(core, "http_token", TOKEN)
    return TOKEN


@pytest.fixture
def make_client(token, monkeypatch, tmp_path):
    """Factory building SDK clients wired to the real API app in-process."""

    def _make(api_key=TOKEN):
        monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path / "data"))
        transport = SyncASGITransport(create_api_app("0.0.0-test"))
        return TrellisClient(api_key=api_key, transport=transport)

    return _make


@pytest.fixture
def sdk_client(make_client):
    client = make_client()
    yield client
    client.close()
