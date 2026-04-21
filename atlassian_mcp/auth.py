"""X-API-Key authentication: FastAPI dependency + Starlette middleware."""
from fastapi import Header, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from atlassian_mcp.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency for /api/v1/* routes."""
    if x_api_key != settings.mcp_api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid X-API-Key header",
        )


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Protect /mcp/* with X-API-Key or Authorization: Bearer."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            key = request.headers.get("x-api-key")
            if not key:
                auth = request.headers.get("authorization", "")
                if auth.lower().startswith("bearer "):
                    key = auth[7:].strip()
            if key != settings.mcp_api_key:
                return JSONResponse(
                    {"detail": "Missing or invalid X-API-Key header"},
                    status_code=401,
                )
        return await call_next(request)
