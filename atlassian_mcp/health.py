"""Health endpoint with Jira + Confluence reachability check."""
import logging

from fastapi import APIRouter

from atlassian_mcp import __version__
from atlassian_mcp.clients import confluence, jira
from atlassian_mcp.config import settings

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", tags=["meta"])
async def health() -> dict:
    result: dict = {
        "status": "ok",
        "version": __version__,
        "jira_url": settings.jira_url,
        "confluence_url": settings.confluence_url,
        "jira_user": None,
        "confluence_user": None,
    }
    errors = []

    try:
        me = jira.myself()
        result["jira_user"] = me.get("name") or me.get("emailAddress")
    except Exception as e:
        errors.append(f"jira: {type(e).__name__}: {e}")

    try:
        me = confluence.get("rest/api/user/current")
        result["confluence_user"] = me.get("username") or me.get("email")
    except Exception as e:
        errors.append(f"confluence: {type(e).__name__}: {e}")

    if errors:
        result["status"] = "degraded: " + " | ".join(errors)

    return result
