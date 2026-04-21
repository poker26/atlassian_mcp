"""Application entry point.

Exposes:
  POST /mcp/       — MCP streamable HTTP transport (X-API-Key or Bearer)
  GET  /api/v1/*   — REST mirror (X-API-Key)
  GET  /health     — liveness + Jira/Confluence reachability
  GET  /docs       — Swagger UI
"""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from atlassian_mcp import __version__, health, rest_api
from atlassian_mcp.auth import MCPAuthMiddleware
from atlassian_mcp.config import settings
from atlassian_mcp.mcp_server import mcp


def create_app() -> FastAPI:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("atlassian_mcp")
    log.info(
        "atlassian-mcp v%s starting on %s:%d, jira=%s, confluence=%s",
        __version__,
        settings.server_host,
        settings.server_port,
        settings.jira_url,
        settings.confluence_url,
    )

    # FastMCP streamable-HTTP sub-app. `path="/"` отключает внутренний
    # префикс /mcp/, который иначе складывается с `app.mount("/mcp", ...)`.
    mcp_app = mcp.http_app(path="/")

    app = FastAPI(
        title="Atlassian MCP",
        version=__version__,
        description=(
            "Atlassian Data Center MCP server (Jira + Confluence).\n\n"
            "- `/mcp/` — MCP streamable HTTP endpoint (X-API-Key or Bearer)\n"
            "- `/api/v1/*` — REST mirror of common operations (X-API-Key)\n"
            "- `/health` — unauthenticated liveness probe\n"
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=mcp_app.lifespan,
    )

    # Auth middleware guards /mcp/*. REST routes use FastAPI Depends.
    app.add_middleware(MCPAuthMiddleware)

    # Routes
    app.include_router(health.router)       # /health (public)
    app.include_router(rest_api.router)     # /api/v1/* (auth via Depends)
    app.mount("/mcp", mcp_app)              # MCP transport (auth via middleware)

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "atlassian_mcp.main:app",
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
