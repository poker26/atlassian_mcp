"""REST mirror (read-only) for common MCP tools.

This module exposes a subset of Confluence/Jira tools as plain HTTP GET
endpoints. Intended for clients that don't speak MCP streamable HTTP —
n8n HTTP Request nodes, curl, shell scripts, ad-hoc integrations.

Write operations (create/update/comment/upload/transition) are intentionally
NOT exposed here. Use MCP for those.

All endpoints require `X-API-Key` header (via FastAPI Depends).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from atlassian_mcp.auth import require_api_key
from atlassian_mcp.tools.common import ToolError
from atlassian_mcp.tools.confluence import (
    confluence_get_page,
    confluence_get_page_children,
    confluence_get_page_comments,
    confluence_get_page_history,
    confluence_list_attachments,
    confluence_list_spaces,
    confluence_search_by_title,
    confluence_search_cql,
)
from atlassian_mcp.tools.jira import jira_get_issue, jira_search


def _handle(fn, *args, **kwargs):
    """Call a tool and convert ToolError into HTTP 502."""
    try:
        return fn(*args, **kwargs)
    except ToolError as e:
        raise HTTPException(status_code=502, detail=str(e))


router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key)],
)


# --------- Jira ---------

@router.get("/jira/search", tags=["jira"], summary="JQL search")
def rest_jira_search(
    jql: str = Query(..., description="JQL query"),
    max_results: int = Query(25, ge=1, le=100),
    fields: str | None = Query(None, description="Comma-separated field list"),
):
    return _handle(jira_search, jql=jql, max_results=max_results, fields=fields)


@router.get("/jira/issues/{issue_key}", tags=["jira"], summary="Full issue details")
def rest_jira_get_issue(issue_key: str):
    return _handle(jira_get_issue, issue_key=issue_key)


# --------- Confluence ---------

@router.get("/confluence/spaces", tags=["confluence"], summary="List spaces")
def rest_confluence_list_spaces(
    limit: int = Query(25, ge=1, le=100),
    start: int = Query(0, ge=0),
):
    return _handle(confluence_list_spaces, limit=limit, start=start)


@router.get("/confluence/pages/{page_id}", tags=["confluence"], summary="Get page")
def rest_confluence_get_page(
    page_id: str,
    include_body: bool = Query(True),
):
    return _handle(confluence_get_page, page_id=page_id, include_body=include_body)


@router.get(
    "/confluence/pages/{page_id}/children",
    tags=["confluence"],
    summary="List child pages",
)
def rest_confluence_get_page_children(
    page_id: str,
    limit: int = Query(50, ge=1, le=200),
    start: int = Query(0, ge=0),
):
    return _handle(
        confluence_get_page_children,
        page_id=page_id,
        limit=limit,
        start=start,
    )


@router.get(
    "/confluence/pages/{page_id}/history",
    tags=["confluence"],
    summary="Page version history",
)
def rest_confluence_get_page_history(
    page_id: str,
    limit: int = Query(25, ge=1, le=100),
):
    return _handle(confluence_get_page_history, page_id=page_id, limit=limit)


@router.get(
    "/confluence/pages/{page_id}/comments",
    tags=["confluence"],
    summary="List page comments",
)
def rest_confluence_get_page_comments(
    page_id: str,
    limit: int = Query(25, ge=1, le=100),
    start: int = Query(0, ge=0),
    location: str = Query("footer", pattern="^(footer|inline|all)$"),
):
    return _handle(
        confluence_get_page_comments,
        page_id=page_id,
        limit=limit,
        start=start,
        location=location,
    )


@router.get(
    "/confluence/pages/{page_id}/attachments",
    tags=["confluence"],
    summary="List page attachments",
)
def rest_confluence_list_attachments(
    page_id: str,
    limit: int = Query(50, ge=1, le=200),
    start: int = Query(0, ge=0),
):
    return _handle(
        confluence_list_attachments,
        page_id=page_id,
        limit=limit,
        start=start,
    )


@router.get(
    "/confluence/search",
    tags=["confluence"],
    summary="CQL search",
)
def rest_confluence_search_cql(
    cql: str = Query(..., description="Confluence Query Language expression"),
    limit: int = Query(25, ge=1, le=50),
):
    return _handle(confluence_search_cql, cql=cql, limit=limit)


@router.get(
    "/confluence/space/{space_key}/pages",
    tags=["confluence"],
    summary="Find page by exact title in space",
)
def rest_confluence_search_by_title(
    space_key: str,
    title: str = Query(..., description="Exact page title"),
    limit: int = Query(10, ge=1, le=10),
):
    return _handle(
        confluence_search_by_title,
        space_key=space_key,
        title=title,
        limit=limit,
    )
