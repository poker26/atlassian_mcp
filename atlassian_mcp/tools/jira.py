"""Jira tools (REST API v2 via atlassian-python-api)."""
from __future__ import annotations

from typing import Any

from atlassian_mcp.clients import jira
from atlassian_mcp.tools.common import ToolError, safe_call


# ----- read -----

def jira_search(jql: str, max_results: int = 25, fields: str | None = None) -> list[dict]:
    """Search Jira issues by JQL.

    Args:
        jql: JQL query (e.g. `project = FF AND status = "In Progress"`).
        max_results: number of issues to return (default 25, up to 100).
        fields: comma-separated field list. Defaults to summary,status,assignee,priority,updated.

    Returns a list of dicts with key, summary, status, assignee, priority, updated, url.
    """
    fields_str = fields or "summary,status,assignee,priority,updated"
    result = safe_call(jira.jql, jql, limit=min(max_results, 100), fields=fields_str)
    issues = result.get("issues", []) if isinstance(result, dict) else []

    out = []
    for i in issues:
        f = i.get("fields", {}) or {}
        key = i.get("key")
        out.append({
            "key": key,
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", "Unknown"),
            "assignee": (f.get("assignee") or {}).get("displayName", "Unassigned"),
            "priority": (f.get("priority") or {}).get("name"),
            "updated": (f.get("updated") or "")[:19],
            "url": f"{jira.url.rstrip('/')}/browse/{key}" if key else None,
        })
    return out


def jira_get_issue(issue_key: str) -> dict:
    """Full details of a Jira issue: status, description, assignee, comments, attachments, labels.

    Args:
        issue_key: issue key like 'FF-560' or 'PP-240'.
    """
    data = safe_call(jira.issue, issue_key, expand="renderedFields")
    f = data.get("fields", {}) or {}

    attachments = [
        {
            "id": a.get("id"),
            "filename": a.get("filename"),
            "size_bytes": a.get("size"),
            "mime": a.get("mimeType"),
            "created": a.get("created"),
            "author": (a.get("author") or {}).get("displayName"),
        }
        for a in (f.get("attachment") or [])
    ]
    comments = [
        {
            "id": c.get("id"),
            "author": (c.get("author") or {}).get("displayName"),
            "created": c.get("created"),
            "body": c.get("body", ""),
        }
        for c in ((f.get("comment") or {}).get("comments") or [])
    ]

    return {
        "key": issue_key,
        "url": f"{jira.url.rstrip('/')}/browse/{issue_key}",
        "summary": f.get("summary", ""),
        "description": f.get("description", ""),
        "status": (f.get("status") or {}).get("name", "Unknown"),
        "priority": (f.get("priority") or {}).get("name"),
        "issue_type": (f.get("issuetype") or {}).get("name"),
        "assignee": (f.get("assignee") or {}).get("displayName", "Unassigned"),
        "reporter": (f.get("reporter") or {}).get("displayName"),
        "labels": f.get("labels") or [],
        "components": [c.get("name") for c in (f.get("components") or [])],
        "created": (f.get("created") or "")[:19],
        "updated": (f.get("updated") or "")[:19],
        "attachments": attachments,
        "comments": comments,
    }


# ----- write -----

def jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    issue_type: str = "Task",
    priority: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """Create a new Jira issue.

    Args:
        project_key: project key (e.g. 'FF', 'PP').
        summary: issue title (required).
        description: issue description in Jira wiki markup or plain text.
        issue_type: issue type name (default 'Task'). Must exist in the project.
        priority: priority name (e.g. 'High', 'Normal'). Omit to use project default.
        assignee: assignee username (not display name).
        labels: list of labels.

    Returns {key, url, summary}.
    """
    fields: dict[str, Any] = {
        "project": {"key": project_key.upper()},
        "summary": summary,
        "description": description,
        "issuetype": {"name": issue_type},
    }
    if priority:
        fields["priority"] = {"name": priority}
    if assignee:
        fields["assignee"] = {"name": assignee}
    if labels:
        fields["labels"] = list(labels)

    result = safe_call(jira.create_issue, fields=fields)
    key = result.get("key")
    if not key:
        raise ToolError(f"Unexpected Jira response: {result}")
    return {
        "key": key,
        "url": f"{jira.url.rstrip('/')}/browse/{key}",
        "summary": summary,
    }


def jira_update_issue(
    issue_key: str,
    summary: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """Update one or more fields of an existing Jira issue.

    Only non-None arguments are sent. Labels fully replace the existing list.
    Use jira_transition_issue to change status (status is not a field update).

    Args:
        issue_key: issue key like 'FF-560'.
        summary, description, priority, assignee, labels: optional fields to update.

    Returns {key, updated_fields}.
    """
    fields: dict[str, Any] = {}
    if summary is not None:
        fields["summary"] = summary
    if description is not None:
        fields["description"] = description
    if priority is not None:
        fields["priority"] = {"name": priority}
    if assignee is not None:
        fields["assignee"] = {"name": assignee}
    if labels is not None:
        fields["labels"] = list(labels)

    if not fields:
        raise ToolError("No fields provided to update")

    safe_call(jira.update_issue_field, issue_key, fields)
    return {
        "key": issue_key,
        "url": f"{jira.url.rstrip('/')}/browse/{issue_key}",
        "updated_fields": list(fields.keys()),
    }


def jira_add_comment(issue_key: str, comment: str) -> dict:
    """Add a comment to a Jira issue.

    Args:
        issue_key: issue key like 'FF-560'.
        comment: comment body (Jira wiki markup or plain text).

    Returns {key, comment_id, author, created}.
    """
    res = safe_call(jira.issue_add_comment, issue_key, comment)
    return {
        "key": issue_key,
        "comment_id": res.get("id"),
        "author": (res.get("author") or {}).get("displayName"),
        "created": res.get("created"),
    }


def jira_transition_issue(
    issue_key: str,
    transition: str,
    comment: str | None = None,
) -> dict:
    """Transition a Jira issue to a new status.

    Args:
        issue_key: issue key like 'FF-560'.
        transition: transition name (case-insensitive) or numeric id.
                    Pass an unknown value to get the list of available transitions.
        comment: optional comment to add during transition.

    Returns {key, new_status, transition_used} on success, or {error, available_transitions} if not found.
    """
    available = safe_call(jira.get_issue_transitions, issue_key) or []
    if isinstance(available, dict):
        available = available.get("transitions", [])

    def _to_name(entry: dict) -> str:
        """'to' may be a string or a {name: ...} dict depending on API version."""
        to = entry.get("to")
        if isinstance(to, dict):
            return to.get("name") or ""
        if isinstance(to, str):
            return to
        return ""

    target = None
    needle = str(transition).strip().lower()
    for t in available:
        if str(t.get("id")) == needle:
            target = t
            break
        if (t.get("name") or "").lower() == needle:
            target = t
            break
        if _to_name(t).lower() == needle:
            target = t
            break

    if not target:
        return {
            "error": f"Transition '{transition}' not found for {issue_key}",
            "available_transitions": [
                {"id": t.get("id"), "name": t.get("name"), "to": _to_name(t)}
                for t in available
            ],
        }

    safe_call(
        jira.set_issue_status_by_transition_id,
        issue_key,
        target.get("id"),
    )

    if comment:
        safe_call(jira.issue_add_comment, issue_key, comment)

    return {
        "key": issue_key,
        "url": f"{jira.url.rstrip('/')}/browse/{issue_key}",
        "new_status": _to_name(target) or target.get("name"),
        "transition_used": {"id": target.get("id"), "name": target.get("name")},
    }

TOOLS = [
    jira_search,
    jira_get_issue,
    jira_create_issue,
    jira_update_issue,
    jira_add_comment,
    jira_transition_issue,
]
