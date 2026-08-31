"""
Static bearer-token auth for the MCP server.

Deliberately not using the MCP SDK's built-in OAuth machinery
(TokenVerifier / AuthSettings) here — that path is designed for full
OAuth 2.1 (issuer discovery, protected resource metadata, dynamic client
registration), which the MCP auth spec makes OPTIONAL for HTTP transports
and which exists to support arbitrary third-party clients. This server has
exactly one legitimate client (your own website backend), so a shared
secret checked on every request is the proportionate amount of auth.

If this server is ever opened up to arbitrary external MCP clients (their
own Claude Desktop, their own agents), swap this middleware for real
OAuth 2.1 via the SDK's token_verifier/auth_server_provider support.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Starlette, expected_token: str) -> None:
        super().__init__(app)
        self._expected = f"Bearer {expected_token}"

    async def dispatch(self, request: Request, call_next):
        header = request.headers.get("authorization", "")
        if header != self._expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
