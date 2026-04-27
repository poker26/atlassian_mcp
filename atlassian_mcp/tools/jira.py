"""Jira tools (REST API v2 via atlassian-python-api)."""
from __future__ import annotations

from typing import Any

import requests

from atlassian_mcp.clients import jira
from atlassian_mcp.config import settings
from atlassian_mcp.tools.common import (
    ToolError,
    b64decode_to_bytes,
    b64encode_bytes,
    envelope_full,
    safe_call,
)
from atlassian_mcp.tools.url_fetch import fetch_url


def _base() -> str:
    return jira.url.rstrip("/")


def _browse(key: str) -> str:
    return f"{_base()}/browse/{key}"


# --------- issue shapes ---------

def _issue_digest(i: dict) -> dict:
    """Flat compact representation suitable for digests / lists."""
    f = i.get("fields", {}) or {}
    key = i.get("key")
    return {
        "key": key,
        "summary": f.get("summary", ""),
        "status": (f.get("status") or {}).get("name", "Unknown"),
        "assignee": (f.get("assignee") or {}).get("displayName", "Unassigned"),
        "priority": (f.get("priority") or {}).get("name"),
        "updated": (f.get("updated") or "")[:19],
        "url": _browse(key) if key else None,
    }


def _issue_full(data: dict, key: str) -> dict:
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
        "key": key,
        "url": _browse(key),
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


# ----- read -----

def jira_search(
    jql: str,
    max_results: int = 25,
    fields: str | None = None,
    start_at: int = 0,
    preset: str = "digest",
) -> dict:
    """Search Jira issues by JQL with pagination.

    Args:
        jql: JQL query.
        max_results: page size (default 25, up to 100).
        fields: comma-separated field list. Defaults depend on preset.
        start_at: pagination offset. Use next_start_at from a prior call.
        preset: 'digest' (flat compact issues) or 'full' (same shape as
                jira_get_issue for each item). Default 'digest'.

    Returns:
        {
          "issues": [...],
          "pagination": {"start_at", "max_results", "total", "is_last",
                         "next_start_at" (None if is_last)}
        }
    """
    if preset not in ("digest", "full"):
        raise ToolError("preset must be 'digest' or 'full'")

    if fields is None:
        fields = (
            "summary,status,assignee,priority,updated"
            if preset == "digest"
            else "*all"
        )

    limit = min(max_results, 100)
    raw = safe_call(
        jira.jql,
        jql,
        fields=fields,
        start=start_at,
        limit=limit,
    )
    issues_raw = raw.get("issues", []) if isinstance(raw, dict) else []
    total = raw.get("total", len(issues_raw)) if isinstance(raw, dict) else len(issues_raw)

    if preset == "digest":
        issues = [_issue_digest(i) for i in issues_raw]
    else:
        issues = [_issue_full(i, i.get("key")) for i in issues_raw]

    returned = len(issues_raw)
    is_last = (start_at + returned) >= total
    return {
        "issues": issues,
        "pagination": {
            "start_at": start_at,
            "max_results": limit,
            "total": total,
            "is_last": is_last,
            "next_start_at": None if is_last else start_at + returned,
        },
    }


def jira_get_issue(issue_key: str) -> dict:
    """Full details of a Jira issue: status, description, assignee, comments, attachments, labels.

    Args:
        issue_key: issue key like 'FF-560' or 'PP-240'.
    """
    data = safe_call(jira.issue, issue_key, expand="renderedFields")
    return _issue_full(data, issue_key)


def jira_get_transitions(issue_key: str) -> list[dict]:
    """List available workflow transitions for an issue from its current status.

    Pair this with jira_transition_issue — use the id or name returned here.

    Args:
        issue_key: issue key like 'FF-560'.

    Returns list of {id, name, to_status}.
    """
    raw = safe_call(jira.get_issue_transitions, issue_key) or []
    if isinstance(raw, dict):
        raw = raw.get("transitions", [])

    def _to_name(entry: dict) -> str:
        to = entry.get("to")
        if isinstance(to, dict):
            return to.get("name") or ""
        if isinstance(to, str):
            return to
        return ""

    return [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "to_status": _to_name(t),
        }
        for t in raw
    ]


def jira_get_changelog(
    issue_key: str,
    since: str | None = None,
    until: str | None = None,
    fields: list[str] | None = None,
) -> dict:
    """Get changelog (status/assignee/priority/... transitions) for a Jira issue.

    On Jira DC, changelog is fetched via GET /rest/api/2/issue/{key}?expand=changelog,
    which returns up to ~100 history entries in a single response. For typical
    issues this is enough; very long histories may be truncated by the server.

    Args:
        issue_key: issue key.
        since: ISO timestamp (e.g. '2026-04-22T00:00:00Z'); only entries with
               `created >= since` are returned. Compared as strings, so both
               bounds should be in the same timezone (UTC recommended).
        until: ISO timestamp; only entries with `created < until` are returned.
        fields: restrict to items that changed specific fields (e.g. ['status',
               'assignee']). If None, all field changes are returned.

    Returns list of {id, created, author, items: [{field, from, fromString,
                                                   to, toString}]}
    ordered oldest -> newest.
    """
    data = safe_call(jira.issue, issue_key, expand="changelog")
    changelog = (data.get("changelog") or {})
    histories = changelog.get("histories") or []

    field_set = {f.lower() for f in fields} if fields else None

    out: list[dict] = []
    for h in histories:
        created = h.get("created", "")
        if since and created < since:
            continue
        if until and created >= until:
            continue

        items = h.get("items") or []
        if field_set is not None:
            items = [it for it in items if (it.get("field") or "").lower() in field_set]
            if not items:
                continue

        author = h.get("author") or {}
        out.append({
            "id": h.get("id"),
            "created": created,
            "author": author.get("displayName") or author.get("name"),
            "items": [
                {
                    "field": it.get("field"),
                    "from": it.get("from"),
                    "fromString": it.get("fromString"),
                    "to": it.get("to"),
                    "toString": it.get("toString"),
                }
                for it in items
            ],
        })

    out.sort(key=lambda e: e.get("created") or "")
    return envelope_full(out)


# ----- write -----

def jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    issue_type: str = "Task",
    priority: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    custom_fields: dict | None = None,
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
        custom_fields: dict of custom field values, merged into the issue payload
                       as-is. Keys MUST be the customfield_XXXXX IDs (use
                       jira_list_fields or jira_get_create_meta to discover them).
                       Values must be in the exact shape Jira expects for that
                       field type — see jira_get_create_meta for allowedValues
                       and schema. Examples:
                         {"customfield_10101": {"id": "10500"}}      # single-select
                         {"customfield_10102": [{"id": "10501"}]}    # multi-select
                         {"customfield_10103": "free text"}          # text
                         {"customfield_10104": {"name": "username"}} # user picker
                       Standard fields (summary, description, etc.) cannot be
                       set through this dict — use the dedicated arguments.

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

    if custom_fields:
        # Sanity check — only customfield_* keys, and don't let custom_fields
        # silently override the standard fields above (would be confusing).
        reserved = {"project", "summary", "description", "issuetype",
                    "priority", "assignee", "labels"}
        for cf_key, cf_value in custom_fields.items():
            if cf_key in reserved:
                raise ToolError(
                    f"custom_fields cannot set standard field '{cf_key}'. "
                    f"Use the dedicated argument instead."
                )
            if not cf_key.startswith("customfield_"):
                raise ToolError(
                    f"custom_fields keys must start with 'customfield_', "
                    f"got '{cf_key}'. Use jira_list_fields to find the right ID."
                )
            fields[cf_key] = cf_value

    result = safe_call(jira.create_issue, fields=fields)
    key = result.get("key")
    if not key:
        raise ToolError(f"Unexpected Jira response: {result}")
    return {"key": key, "url": _browse(key), "summary": summary}


def jira_update_issue(
    issue_key: str,
    summary: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    custom_fields: dict | None = None,
) -> dict:
    """Update one or more fields of an existing Jira issue.

    Only non-None arguments are sent. Labels fully replace the existing list.
    Use jira_transition_issue to change status (status is not a field update).

    Args:
        custom_fields: dict of custom field values, merged into the update
                       payload. Keys MUST be customfield_XXXXX IDs. Values must
                       be in the exact shape Jira expects (see jira_create_issue
                       docstring for examples). Pass None as a value to clear
                       a field, e.g. {"customfield_10101": None}.
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

    if custom_fields:
        reserved = {"summary", "description", "priority", "assignee", "labels"}
        for cf_key, cf_value in custom_fields.items():
            if cf_key in reserved:
                raise ToolError(
                    f"custom_fields cannot set standard field '{cf_key}'. "
                    f"Use the dedicated argument instead."
                )
            if not cf_key.startswith("customfield_"):
                raise ToolError(
                    f"custom_fields keys must start with 'customfield_', "
                    f"got '{cf_key}'. Use jira_list_fields to find the right ID."
                )
            fields[cf_key] = cf_value

    if not fields:
        raise ToolError("No fields provided to update")

    safe_call(jira.update_issue_field, issue_key, fields)
    return {
        "key": issue_key,
        "url": _browse(issue_key),
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
                    Use jira_get_transitions to discover available options.
        comment: optional comment to add during transition.

    Returns {key, new_status, transition_used} on success, or
            {error, available_transitions} if not found.
    """
    available = jira_get_transitions(issue_key)

    needle = str(transition).strip().lower()
    target = None
    for t in available:
        if str(t.get("id")) == needle:
            target = t
            break
        if (t.get("name") or "").lower() == needle:
            target = t
            break
        if (t.get("to_status") or "").lower() == needle:
            target = t
            break

    if not target:
        return {
            "error": f"Transition '{transition}' not found for {issue_key}",
            "available_transitions": available,
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
        "url": _browse(issue_key),
        "new_status": target.get("to_status") or target.get("name"),
        "transition_used": {"id": target.get("id"), "name": target.get("name")},
    }


# --------- attachments ---------

def jira_list_attachments(issue_key: str) -> dict:
    """List attachments on a Jira issue.

    Args:
        issue_key: issue key like 'FF-560'.

    Returns {results: [{id, filename, size_bytes, mime, created, author, download_url}], pagination}.
    """
    data = safe_call(jira.issue, issue_key, fields="attachment")
    fields_ = data.get("fields", {}) or {}
    atts = fields_.get("attachment") or []
    out = []
    for a in atts:
        author = a.get("author") or {}
        out.append({
            "id": a.get("id"),
            "filename": a.get("filename"),
            "size_bytes": a.get("size"),
            "mime": a.get("mimeType"),
            "created": a.get("created"),
            "author": author.get("displayName") or author.get("name"),
            "download_url": a.get("content"),
        })
    return envelope_full(out)


def jira_get_attachment(attachment_id: str) -> dict:
    """Download a Jira attachment as base64.

    Size limited by MAX_ATTACHMENT_SIZE (default 2 MB). Larger attachments fail
    with a clear error -- use the download_url from jira_list_attachments to
    fetch them directly via HTTP.

    Args:
        attachment_id: attachment id.

    Returns {id, filename, mime, size_bytes, data_base64}.
    """
    meta = safe_call(
        jira.get,
        f"rest/api/2/attachment/{attachment_id}",
    )
    content_url = meta.get("content")
    if not content_url:
        raise ToolError(f"No content URL for attachment {attachment_id}")

    declared_size = meta.get("size") or 0
    if declared_size and declared_size > settings.max_attachment_size:
        raise ToolError(
            f"Attachment too large ({declared_size} bytes > "
            f"{settings.max_attachment_size}). "
            f"Use download_url directly: {content_url}"
        )

    headers = {"Authorization": f"Bearer {settings.jira_pat}"}
    with requests.get(
        content_url,
        headers=headers,
        verify=settings.verify,
        stream=True,
        timeout=30,
    ) as resp:
        resp.raise_for_status()
        data = bytearray()
        for chunk in resp.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > settings.max_attachment_size:
                raise ToolError(
                    f"Attachment exceeded {settings.max_attachment_size} bytes "
                    f"while streaming. Use download_url directly: {content_url}"
                )

    return {
        "id": attachment_id,
        "filename": meta.get("filename"),
        "mime": meta.get("mimeType"),
        "size_bytes": len(data),
        "data_base64": b64encode_bytes(bytes(data)),
    }


def _jira_upload_raw(
    issue_key: str,
    filename: str,
    data: bytes,
    mime: str | None,
) -> list[dict]:
    """Low-level multipart upload to Jira. Returns list of attachment dicts."""
    url = f"{_base()}/rest/api/2/issue/{issue_key}/attachments"
    headers = {
        "Authorization": f"Bearer {settings.jira_pat}",
        "X-Atlassian-Token": "no-check",
    }
    files = {
        "file": (filename, data, mime or "application/octet-stream"),
    }
    resp = requests.post(
        url,
        headers=headers,
        files=files,
        verify=settings.verify,
        timeout=60,
    )
    if not resp.ok:
        raise ToolError(
            f"Jira attachment upload failed: HTTP {resp.status_code} "
            f"{resp.text[:500]}"
        )
    result = resp.json()
    if not isinstance(result, list):
        raise ToolError(f"Unexpected Jira attachment response: {result}")
    return result


def jira_upload_attachment(
    issue_key: str,
    filename: str,
    data_base64: str,
    mime: str | None = None,
) -> dict:
    """Upload an attachment to a Jira issue from base64.

    Size limited by MAX_ATTACHMENT_SIZE (default 2 MB). For larger files use
    jira_attach_from_url with a public download URL.

    Args:
        issue_key: issue key.
        filename: attachment filename.
        data_base64: file content, base64-encoded.
        mime: optional MIME type. If omitted, Atlassian guesses from filename.

    Returns {id, filename, size_bytes, mime, download_url}.
    """
    raw = b64decode_to_bytes(data_base64)
    if len(raw) > settings.max_attachment_size:
        raise ToolError(
            f"Attachment too large ({len(raw)} bytes > "
            f"{settings.max_attachment_size}). "
            f"Use jira_attach_from_url instead."
        )
    results = _jira_upload_raw(issue_key, filename, raw, mime)
    a = results[0]
    return {
        "id": a.get("id"),
        "filename": a.get("filename") or filename,
        "size_bytes": a.get("size"),
        "mime": a.get("mimeType"),
        "download_url": a.get("content"),
    }


def jira_attach_from_url(
    issue_key: str,
    url: str,
    filename: str | None = None,
    mime: str | None = None,
) -> dict:
    """Download a file from a public URL and attach it to a Jira issue.

    URL validation: only http/https; private/reserved IPs rejected; redirects
    capped at 5. Size capped by MAX_URL_FETCH_SIZE (default 10 MB).

    Args:
        issue_key: issue key.
        url: public http(s) URL to fetch.
        filename: override auto-detection (Content-Disposition -> URL path).
        mime: override Content-Type from response.

    Returns {id, filename, size_bytes, mime, download_url, source_url}.
    """
    fetched = fetch_url(url, filename=filename, mime=mime)
    results = _jira_upload_raw(issue_key, fetched.filename, fetched.data, fetched.mime)
    a = results[0]
    return {
        "id": a.get("id"),
        "filename": a.get("filename") or fetched.filename,
        "size_bytes": a.get("size") or len(fetched.data),
        "mime": a.get("mimeType") or fetched.mime,
        "download_url": a.get("content"),
        "source_url": url,
    }


# --------- links ---------

def jira_get_links(issue_key: str) -> dict:
    """Get issue links (blocks, relates, duplicates, ...) for a Jira issue.

    Args:
        issue_key: issue key.

    Returns {results: [{id, type, direction, target_key, target_summary, target_status}], pagination}.
            direction is 'inward' or 'outward' relative to this issue.
    """
    data = safe_call(jira.issue, issue_key, fields="issuelinks")
    links = ((data.get("fields") or {}).get("issuelinks") or [])
    out = []
    for l in links:
        t = l.get("type") or {}
        outward = l.get("outwardIssue")
        inward = l.get("inwardIssue")
        if outward:
            target = outward
            direction = "outward"
            type_label = t.get("outward") or t.get("name")
        elif inward:
            target = inward
            direction = "inward"
            type_label = t.get("inward") or t.get("name")
        else:
            continue
        tf = target.get("fields") or {}
        out.append({
            "id": l.get("id"),
            "type": type_label,
            "type_name": t.get("name"),
            "direction": direction,
            "target_key": target.get("key"),
            "target_summary": tf.get("summary"),
            "target_status": (tf.get("status") or {}).get("name"),
            "target_url": _browse(target.get("key")) if target.get("key") else None,
        })
    return envelope_full(out)


def jira_add_link(
    from_key: str,
    to_key: str,
    link_type: str = "Relates",
) -> dict:
    """Create an issue link between two Jira issues.

    Args:
        from_key: source issue key (the "outward" side).
        to_key: target issue key (the "inward" side).
        link_type: link type name ('Blocks', 'Relates', 'Duplicate',
                   'Cloners', ...). Case-sensitive on some DC builds;
                   use the exact name shown in Issue -> Link dialog.

    Returns {from_key, to_key, link_type}.
    """
    payload = {
        "type": {"name": link_type},
        "inwardIssue": {"key": to_key},
        "outwardIssue": {"key": from_key},
    }
    safe_call(
        jira.post,
        "rest/api/2/issueLink",
        data=payload,
    )
    return {
        "from_key": from_key,
        "to_key": to_key,
        "link_type": link_type,
    }


def jira_add_remote_link(
    issue_key: str,
    url: str,
    title: str,
    summary: str | None = None,
    icon_url: str | None = None,
) -> dict:
    """Add a remote link (web URL) to a Jira issue.

    Typical use: link a Jira issue to a GitLab MR, a Confluence page, or an
    external doc. Unlike issue-to-issue links (jira_add_link), remote links
    can point anywhere on the web.

    Args:
        issue_key: Jira issue key.
        url: remote URL.
        title: link title shown in Jira UI.
        summary: optional longer description.
        icon_url: optional favicon/icon URL.

    Returns {issue_key, remote_link_id, url, title}.
    """
    object_ = {"url": url, "title": title}
    if summary:
        object_["summary"] = summary
    if icon_url:
        object_["icon"] = {"url16x16": icon_url, "title": title}

    payload = {"object": object_}
    result = safe_call(
        jira.post,
        f"rest/api/2/issue/{issue_key}/remotelink",
        data=payload,
    )
    return {
        "issue_key": issue_key,
        "remote_link_id": result.get("id") if isinstance(result, dict) else None,
        "url": url,
        "title": title,
    }


# --------- project metadata helpers ---------

def jira_list_labels(project_key: str | None = None) -> dict:
    """List labels known to Jira. Optionally scope to a specific project.

    Args:
        project_key: if given, only labels used in issues of this project.

    Returns sorted list of label names.
    """
    if project_key:
        jql = f'project = "{project_key.upper()}" AND labels is not EMPTY'
        raw = safe_call(jira.jql, jql, fields="labels", limit=100)
        issues = raw.get("issues", []) if isinstance(raw, dict) else []
        seen: set[str] = set()
        for i in issues:
            for lbl in ((i.get("fields") or {}).get("labels") or []):
                seen.add(lbl)
        return envelope_full(sorted(seen))

    raw = safe_call(jira.get, "rest/api/2/label")
    if isinstance(raw, dict) and "values" in raw:
        return envelope_full(sorted(raw.get("values") or []))
    return envelope_full([])


def jira_list_components(project_key: str) -> dict:
    """List components for a Jira project.

    Args:
        project_key: project key.

    Returns list of {id, name, description, lead}.
    """
    raw = safe_call(
        jira.get,
        f"rest/api/2/project/{project_key.upper()}/components",
    )
    comps = raw if isinstance(raw, list) else []
    return envelope_full([
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "description": c.get("description"),
            "lead": (c.get("lead") or {}).get("displayName")
                    or (c.get("lead") or {}).get("name"),
        }
        for c in comps
    ])


def jira_list_versions(project_key: str) -> dict:
    """List fix versions / releases for a Jira project.

    Args:
        project_key: project key.

    Returns list of {id, name, released, archived, release_date, start_date}.
    """
    raw = safe_call(
        jira.get,
        f"rest/api/2/project/{project_key.upper()}/versions",
    )
    versions = raw if isinstance(raw, list) else []
    return envelope_full([
        {
            "id": v.get("id"),
            "name": v.get("name"),
            "released": v.get("released"),
            "archived": v.get("archived"),
            "release_date": v.get("releaseDate"),
            "start_date": v.get("startDate"),
        }
        for v in versions
    ])


# --------- users ---------

def jira_get_current_user() -> dict:
    """Return the currently authenticated Jira user (i.e. the owner of JIRA_PAT).

    Returns {name, key, email, displayName, active, timeZone}.
    """
    data = safe_call(jira.myself)
    return {
        "name": data.get("name"),
        "key": data.get("key"),
        "email": data.get("emailAddress"),
        "displayName": data.get("displayName"),
        "active": data.get("active"),
        "timeZone": data.get("timeZone"),
    }


def jira_search_users(
    query: str,
    max_results: int = 25,
    include_inactive: bool = False,
) -> dict:
    """Search Jira users by username, email, or displayName fragment.

    Returns list of {name, key, email, displayName, active}.
    """
    raw = safe_call(
        jira.get,
        "rest/api/2/user/search",
        params={
            "username": query,
            "maxResults": min(max_results, 50),
            "includeInactive": str(include_inactive).lower(),
        },
    )
    users = raw if isinstance(raw, list) else (
        raw.get("users", []) if isinstance(raw, dict) else []
    )
    return envelope_full([
        {
            "name": u.get("name"),
            "key": u.get("key"),
            "email": u.get("emailAddress"),
            "displayName": u.get("displayName"),
            "active": u.get("active"),
        }
        for u in users
    ])


TOOLS = [
    jira_search,
    jira_get_issue,
    jira_get_transitions,
    jira_get_changelog,
    jira_create_issue,
    jira_update_issue,
    jira_add_comment,
    jira_transition_issue,
    jira_list_attachments,
    jira_get_attachment,
    jira_upload_attachment,
    jira_attach_from_url,
    jira_get_links,
    jira_add_link,
    jira_add_remote_link,
    jira_list_labels,
    jira_list_components,
    jira_list_versions,
    jira_get_current_user,
    jira_search_users,
]
