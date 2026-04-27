"""REST mirror (read-only) for common MCP tools.

All endpoints require `X-API-Key` header (via FastAPI Depends).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from atlassian_mcp.auth import require_api_key
from atlassian_mcp.tools.common import ToolError
from atlassian_mcp.tools.confluence import (
    confluence_get_current_user,
    confluence_get_page,
    confluence_get_user,
    confluence_get_page_children,
    confluence_get_page_comments,
    confluence_get_page_history,
    confluence_list_attachments,
    confluence_list_spaces,
    confluence_search_by_title,
    confluence_search_cql,
    confluence_search_users,
)
from atlassian_mcp.tools.jira import (
    jira_get_changelog,
    jira_get_current_user,
    jira_get_issue,
    jira_get_links,
    jira_get_transitions,
    jira_list_attachments,
    jira_list_components,
    jira_list_labels,
    jira_list_versions,
    jira_search,
    jira_search_users,
)


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

@router.get("/jira/search", tags=["jira"], summary="JQL search with pagination")
def rest_jira_search(
    jql: str = Query(..., description="JQL query"),
    max_results: int = Query(25, ge=1, le=100),
    start_at: int = Query(0, ge=0),
    fields: str | None = Query(None, description="Comma-separated field list"),
    preset: str = Query("digest", pattern="^(digest|full)$"),
):
    return _handle(
        jira_search,
        jql=jql,
        max_results=max_results,
        start_at=start_at,
        fields=fields,
        preset=preset,
    )


@router.get("/jira/issues/{issue_key}", tags=["jira"], summary="Full issue details")
def rest_jira_get_issue(issue_key: str):
    return _handle(jira_get_issue, issue_key=issue_key)


@router.get(
    "/jira/issues/{issue_key}/transitions",
    tags=["jira"],
    summary="Available workflow transitions",
)
def rest_jira_get_transitions(issue_key: str):
    return _handle(jira_get_transitions, issue_key=issue_key)


@router.get(
    "/jira/issues/{issue_key}/changelog",
    tags=["jira"],
    summary="Issue changelog (history of field changes)",
)
def rest_jira_get_changelog(
    issue_key: str,
    since: str | None = Query(None, description="ISO timestamp lower bound"),
    until: str | None = Query(None, description="ISO timestamp upper bound"),
    fields: str | None = Query(None, description="Comma-separated fields filter"),
):
    field_list = [f.strip() for f in fields.split(",")] if fields else None
    return _handle(
        jira_get_changelog,
        issue_key=issue_key,
        since=since,
        until=until,
        fields=field_list,
    )


@router.get(
    "/jira/issues/{issue_key}/attachments",
    tags=["jira"],
    summary="List attachments on issue",
)
def rest_jira_list_attachments(issue_key: str):
    return _handle(jira_list_attachments, issue_key=issue_key)


@router.get(
    "/jira/issues/{issue_key}/links",
    tags=["jira"],
    summary="Issue-to-issue links",
)
def rest_jira_get_links(issue_key: str):
    return _handle(jira_get_links, issue_key=issue_key)


@router.get(
    "/jira/projects/{project_key}/components",
    tags=["jira"],
    summary="List project components",
)
def rest_jira_list_components(project_key: str):
    return _handle(jira_list_components, project_key=project_key)


@router.get(
    "/jira/projects/{project_key}/versions",
    tags=["jira"],
    summary="List project versions / releases",
)
def rest_jira_list_versions(project_key: str):
    return _handle(jira_list_versions, project_key=project_key)


@router.get(
    "/jira/labels",
    tags=["jira"],
    summary="List labels (optionally scoped to project)",
)
def rest_jira_list_labels(
    project_key: str | None = Query(None),
):
    return _handle(jira_list_labels, project_key=project_key)


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


# --------- Users ---------

@router.get("/jira/me", tags=["jira"], summary="Current Jira user")
def rest_jira_me():
    return _handle(jira_get_current_user)


@router.get("/jira/users", tags=["jira"], summary="Search Jira users")
def rest_jira_search_users(
    query: str = Query(..., description="Username/email/displayName fragment"),
    max_results: int = Query(25, ge=1, le=50),
    include_inactive: bool = Query(False),
):
    return _handle(
        jira_search_users,
        query=query,
        max_results=max_results,
        include_inactive=include_inactive,
    )


@router.get("/confluence/me", tags=["confluence"], summary="Current Confluence user")
def rest_confluence_me():
    return _handle(confluence_get_current_user)


@router.get(
    "/confluence/users/{identifier}",
    tags=["confluence"],
    summary="Get Confluence user by username or key",
)
def rest_confluence_get_user(
    identifier: str,
    by: str = Query("username", pattern="^(username|key)$"),
):
    return _handle(confluence_get_user, identifier=identifier, by=by)


@router.get("/confluence/users", tags=["confluence"], summary="Search Confluence users")
def rest_confluence_search_users(
    query: str = Query(..., description="Displayname/username fragment"),
    limit: int = Query(25, ge=1, le=50),
):
    return _handle(confluence_search_users, query=query, limit=limit)
