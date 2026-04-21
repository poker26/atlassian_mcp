"""Shared Jira and Confluence client instances."""
import logging

from atlassian import Confluence, Jira

from atlassian_mcp.config import settings

log = logging.getLogger(__name__)

def _make_jira() -> Jira:
    log.info("Initializing Jira client for %s", settings.jira_url)
    return Jira(
        url=settings.jira_url,
        token=settings.jira_pat,
        verify_ssl=settings.verify,
        cloud=False,
    )


def _make_confluence() -> Confluence:
    log.info("Initializing Confluence client for %s", settings.confluence_url)
    return Confluence(
        url=settings.confluence_url,
        token=settings.confluence_pat,
        verify_ssl=settings.verify,
        cloud=False,
    )


jira = _make_jira()
confluence = _make_confluence()
