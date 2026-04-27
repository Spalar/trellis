from __future__ import annotations

import hmac
import os

from fastmcp.exceptions import ToolError


def _is_no_auth_enabled() -> bool:
    return os.getenv("TRELLIS_ALLOW_NO_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}


def validate_auth(request_headers: dict) -> None:
    # No-auth mode takes precedence (for local development)
    if _is_no_auth_enabled():
        return

    api_key = os.getenv("TRELLIS_API_KEY", "").strip()
    if not api_key:
        raise ToolError("Unauthorized: server API key is not configured")

    headers = {str(key).lower(): value for key, value in request_headers.items()}
    auth = str(headers.get("authorization", ""))
    if not auth.startswith("Bearer "):
        raise ToolError("Unauthorized")

    token = auth[7:]
    if not hmac.compare_digest(token, api_key):
        raise ToolError("Unauthorized")