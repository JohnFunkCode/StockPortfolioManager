"""Shared HTTP client for the MCP gateway wrappers (architectural standard v2 §5.5).

Phase 3 inverts the five MCP servers onto the REST tier: each ``@mcp.tool()`` is a
thin wrapper that translates a tool call into an HTTP request against the FastAPI
front door (Rule 6 — ``AI Agent → MCP wrapper → REST tier → Service``). This module
is the *single seam* through which every wrapper reaches that front door, so base-URL
configuration, auth-header forwarding, and error mapping live in exactly one place.

Configuration (env):
    QUANTCORE_REST_URL      base URL of the REST tier (default ``http://127.0.0.1:5001``)
    QUANTCORE_REST_TIMEOUT  per-request timeout in seconds (default ``60``)

Auth: a per-call bearer token may be supplied. Phase 3 Step 6 wires this from the
incoming MCP request metadata for identity passthrough (§8); local / no-auth callers
omit it and the header is simply not sent.

Usage in a wrapper tool body::

    from mcp_gateway import rest_client

    @mcp.tool()
    def get_short_interest(symbol: str) -> dict:
        '''<curated LLM-facing docstring preserved verbatim>'''
        return rest_client.get(f"/api/securities/{symbol}/microstructure")

On a non-2xx response ``RestError`` is raised carrying the front door's parsed JSON
error body, so a wrapper that wants to mirror a service's error-dict *return* (rather
than propagate) can ``except RestError as e: return e.payload``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:5001"
DEFAULT_TIMEOUT = 60.0


def _base_url() -> str:
    return os.environ.get("QUANTCORE_REST_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    raw = os.environ.get("QUANTCORE_REST_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT


class RestError(RuntimeError):
    """Raised when the REST tier returns a non-2xx response.

    Carries the parsed JSON error body (``payload``) so a wrapper can surface the
    front door's exact error dict to the LLM unchanged.
    """

    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"REST tier returned {status_code}: {payload}")


def _incoming_auth_token() -> Optional[str]:
    """Pull the caller's bearer token off the active MCP request, if any (Step 6).

    Identity passthrough: when a wrapper runs over HTTP transport, FastMCP exposes the
    inbound request's headers via ``get_http_headers()``. We lift the ``Authorization``
    header so the same token the agent presented to the wrapper is forwarded to the REST
    tier's JWT check — making the front door the single enforcement point (Rule 6).

    Returns ``None`` outside an HTTP request (e.g. ``main.py``/CLI in-process callers, or
    stdio), so those paths simply send no token.
    """
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers() or {}
    except Exception:
        return None
    auth = headers.get("authorization") or headers.get("Authorization")
    if not auth:
        return None
    auth = auth.strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth


def _headers(auth_token: Optional[str]) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = auth_token or _incoming_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def _handle(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text}
    if response.is_success:
        return payload
    raise RestError(response.status_code, payload)


def get(path: str, *, auth_token: Optional[str] = None, **params: Any) -> Any:
    """``GET {REST}/{path}`` with query params; return parsed JSON or raise ``RestError``.

    ``None``-valued params are dropped. Repeatable list params (e.g.
    ``expirations=[...]``, ``strikes=[...]``) are passed straight to httpx, which
    encodes them as repeated query keys — matching the REST tier's
    ``List[...] = Query(...)`` signatures.
    """
    clean = {k: v for k, v in params.items() if v is not None}
    with httpx.Client(base_url=_base_url(), timeout=_timeout()) as client:
        return _handle(client.get(_path(path), params=clean, headers=_headers(auth_token)))


def post(
    path: str,
    *,
    json: Optional[dict] = None,
    auth_token: Optional[str] = None,
    **params: Any,
) -> Any:
    """``POST {REST}/{path}`` with an optional JSON body + query params."""
    clean = {k: v for k, v in params.items() if v is not None}
    with httpx.Client(base_url=_base_url(), timeout=_timeout()) as client:
        return _handle(
            client.post(_path(path), params=clean, json=json, headers=_headers(auth_token))
        )
