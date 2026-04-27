"""Confluence macro builders for Jira integration.

Returns raw storage-format XML strings of jira macros — to be embedded into
a page body via confluence_create_page or confluence_update_page. These tools
do NOT touch any pages; they only assemble XML.

Why server_id is mandatory:
  Confluence's jira macros need a `serverId` parameter pointing at a configured
  Application Link to the Jira instance. The applinks API
  (/rest/applinks/1.0/applicationlink) requires admin scope, which the bot
  PAT typically lacks (returns 401 'Only an admin can access this resource').
  So serverId must be supplied by the caller, taken from:
    - Confluence UI → Settings → Application Links → edit the jira applink
    - or storage format of any existing page with a jira macro

Once you know it, set it as a constant in your workflow and pass it to
every call.
"""
from __future__ import annotations

import re
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from atlassian_mcp.tools.common import ToolError


_SERVER_ID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


def _validate_server_id(server_id: str) -> None:
    if not _SERVER_ID_RE.match(server_id):
        raise ToolError(
            f"server_id {server_id!r} is not a UUID — Confluence Application "
            "Link IDs are UUIDs (8-4-4-4-12 hex). Verify the value in "
            "Confluence → Settings → Application Links."
        )


def confluence_make_jira_issue_macro(
    issue_key: str,
    server_id: str,
    server_name: str | None = None,
    show_summary: bool = True,
) -> dict:
    """Build a storage-format XML fragment that renders a single Jira issue.

    The fragment is a self-contained <ac:structured-macro> element. Embed it
    inline in a page body: it renders as the Jira issue card with key, status
    and summary.

    Args:
        issue_key: Jira issue key, e.g. 'PP-308'.
        server_id: UUID of the Confluence Application Link to the Jira
                   instance.
        server_name: human-readable display name of the link (e.g.
                     'jira.inplatlabs.ru'). Optional — Confluence will fall
                     back to applink defaults if omitted.
        show_summary: when True (default), Jira's summary is shown next to
                      the key. When False, only the key is rendered.

    Returns {"xml": "<ac:structured-macro ...>...</ac:structured-macro>",
             "issue_key": ..., "server_id": ...}.
    """
    if not issue_key or not isinstance(issue_key, str):
        raise ToolError("issue_key is required (string)")
    if "-" not in issue_key:
        raise ToolError(
            f"issue_key {issue_key!r} doesn't look like a Jira key "
            "(expected '<PROJECT>-<NUMBER>' like 'PP-308')."
        )
    _validate_server_id(server_id)

    params: list[str] = [
        f'<ac:parameter ac:name="server">{xml_escape(server_name or "")}</ac:parameter>'
        if server_name else "",
        f'<ac:parameter ac:name="serverId">{xml_escape(server_id)}</ac:parameter>',
        f'<ac:parameter ac:name="key">{xml_escape(issue_key)}</ac:parameter>',
    ]
    if not show_summary:
        params.append('<ac:parameter ac:name="showSummary">false</ac:parameter>')

    inner = "".join(p for p in params if p)
    xml = (
        '<ac:structured-macro ac:name="jira" ac:schema-version="1">'
        f'{inner}'
        '</ac:structured-macro>'
    )
    return {"xml": xml, "issue_key": issue_key, "server_id": server_id}


def confluence_make_jira_jql_macro(
    jql: str,
    server_id: str,
    server_name: str | None = None,
    columns: list[str] | None = None,
    max_issues: int = 20,
    count_only: bool = False,
) -> dict:
    """Build a storage-format XML fragment that renders a Jira JQL query.

    The fragment is a self-contained <ac:structured-macro> element. It
    renders as a table of issues (or, when count_only=True, just a number).

    Args:
        jql: JQL query string.
        server_id: UUID of the Confluence Application Link to the Jira
                   instance.
        server_name: human-readable display name of the applink.
        columns: list of Jira field IDs to show as columns. Common values:
                 'key', 'summary', 'status', 'assignee', 'priority', 'updated',
                 'created', 'due'. Pass None to use Confluence defaults
                 (typically key, summary, status, assignee).
        max_issues: cap on rendered issues (default 20). Confluence will not
                    show more even if JQL returns more.
        count_only: when True, the macro renders just a count of matching
                    issues, not a list.

    Returns {"xml": ..., "jql": ..., "server_id": ...}.
    """
    if not jql or not isinstance(jql, str):
        raise ToolError("jql is required (string)")
    _validate_server_id(server_id)
    if max_issues < 1:
        raise ToolError("max_issues must be >= 1")

    params: list[str] = [
        f'<ac:parameter ac:name="server">{xml_escape(server_name or "")}</ac:parameter>'
        if server_name else "",
        f'<ac:parameter ac:name="serverId">{xml_escape(server_id)}</ac:parameter>',
        f'<ac:parameter ac:name="jqlQuery">{xml_escape(jql)}</ac:parameter>',
        f'<ac:parameter ac:name="maximumIssues">{max_issues}</ac:parameter>',
    ]
    if columns:
        cols = ",".join(c.strip() for c in columns if c.strip())
        params.append(
            f'<ac:parameter ac:name="columns">{xml_escape(cols)}</ac:parameter>'
        )
    if count_only:
        params.append('<ac:parameter ac:name="count">true</ac:parameter>')

    inner = "".join(p for p in params if p)
    xml = (
        '<ac:structured-macro ac:name="jira" ac:schema-version="1">'
        f'{inner}'
        '</ac:structured-macro>'
    )
    return {"xml": xml, "jql": jql, "server_id": server_id}


TOOLS = [
    confluence_make_jira_issue_macro,
    confluence_make_jira_jql_macro,
]
