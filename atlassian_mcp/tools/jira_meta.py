"""Jira metadata discovery tools.

Read-only GET endpoints that help the caller understand a Jira instance
before creating or searching issues — what projects exist, what issue types
they accept, what fields are required, what statuses/priorities/etc are
available, who's in which group.

All endpoints are REST API v2 on Jira Data Center.
"""
from __future__ import annotations

from typing import Any

from atlassian_mcp.clients import jira
from atlassian_mcp.tools.common import ToolError, envelope_full, safe_call, sanitize_strings


def _base() -> str:
    return jira.url.rstrip("/")


def _project_url(key: str) -> str:
    return f"{_base()}/projects/{key}"


# --------- projects ---------

def jira_list_projects(query: str | None = None, recent: int = 0) -> dict:
    """List Jira projects accessible to the authenticated user.

    Args:
        query: optional substring to match project name or key (case-insensitive,
               filtered client-side after the API call since DC v2 doesn't
               support 'searchBy' in /project).
        recent: if > 0, ask Jira to return only the N most recently accessed
                projects (uses 'recent' query parameter).

    Returns list of {key, id, name, project_type, lead, url}.
    """
    params: dict[str, Any] = {}
    if recent and recent > 0:
        params["recent"] = recent

    raw = safe_call(jira.get, "rest/api/2/project", params=params or None)
    items = raw if isinstance(raw, list) else []

    if query:
        q = query.lower()
        items = [
            p for p in items
            if q in (p.get("name") or "").lower()
            or q in (p.get("key") or "").lower()
        ]

    out = []
    for p in items:
        lead = p.get("lead") or {}
        out.append({
            "key": p.get("key"),
            "id": p.get("id"),
            "name": p.get("name"),
            "project_type": p.get("projectTypeKey"),
            "lead": lead.get("displayName") or lead.get("name"),
            "url": _project_url(p.get("key")) if p.get("key") else None,
        })
    return sanitize_strings(envelope_full(out))


def jira_get_project(project_key: str) -> dict:
    """Get full details for a Jira project by key.

    Includes issue types, components, lead, description, project category,
    versions count.

    Args:
        project_key: project key (e.g. 'FF', 'PP').

    Returns {key, id, name, description, project_type, lead, url, issue_types,
             components, versions_count, category}.
    """
    data = safe_call(
        jira.get,
        f"rest/api/2/project/{project_key.upper()}",
    )
    lead = data.get("lead") or {}
    category = data.get("projectCategory") or {}

    issue_types = [
        {
            "id": it.get("id"),
            "name": it.get("name"),
            "description": it.get("description"),
            "subtask": it.get("subtask", False),
        }
        for it in (data.get("issueTypes") or [])
    ]
    components = [
        {"id": c.get("id"), "name": c.get("name")}
        for c in (data.get("components") or [])
    ]

    return sanitize_strings({
        "key": data.get("key"),
        "id": data.get("id"),
        "name": data.get("name"),
        "description": data.get("description"),
        "project_type": data.get("projectTypeKey"),
        "lead": lead.get("displayName") or lead.get("name"),
        "url": _project_url(data.get("key")) if data.get("key") else None,
        "issue_types": issue_types,
        "components": components,
        "versions_count": len(data.get("versions") or []),
        "category": {
            "id": category.get("id"),
            "name": category.get("name"),
        } if category else None,
    })


# --------- issue types / statuses ---------

def jira_list_issue_types(project_key: str | None = None) -> dict:
    """List Jira issue types.

    Args:
        project_key: if given, returns only issue types valid in that project
                     (queries /issuetype/project endpoint). Otherwise returns
                     all issue types defined in the instance.

    Returns list of {id, name, description, subtask, icon_url}.
    """
    if project_key:
        # /issuetype/project endpoint is unreliable on Jira DC (returns
        # "Given issue type does not exist" on some configurations).
        # Fall back to /project/{key} which embeds issueTypes in the response
        # — this works consistently.
        proj = safe_call(
            jira.get,
            f"rest/api/2/project/{project_key.upper()}",
        )
        items = proj.get("issueTypes") or []
    else:
        raw = safe_call(jira.get, "rest/api/2/issuetype")
        items = raw if isinstance(raw, list) else []
    return sanitize_strings(envelope_full([
        {
            "id": it.get("id"),
            "name": it.get("name"),
            "description": it.get("description"),
            "subtask": it.get("subtask", False),
            "icon_url": it.get("iconUrl"),
        }
        for it in items
    ]))


def jira_list_statuses(project_key: str) -> dict:
    """List statuses available in a Jira project, grouped by issue type.

    Args:
        project_key: project key.

    Returns list of {issue_type, statuses: [{id, name, category}]}.
    """
    raw = safe_call(
        jira.get,
        f"rest/api/2/project/{project_key.upper()}/statuses",
    )
    items = raw if isinstance(raw, list) else []
    out = []
    for it in items:
        statuses = []
        for s in (it.get("statuses") or []):
            cat = s.get("statusCategory") or {}
            statuses.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "category": cat.get("key") or cat.get("name"),
            })
        out.append({
            "issue_type": it.get("name"),
            "issue_type_id": it.get("id"),
            "statuses": statuses,
        })
    return sanitize_strings(envelope_full(out))


# --------- priorities / resolutions / link types ---------

def jira_list_priorities() -> dict:
    """List all priorities defined in the Jira instance.

    Returns list of {id, name, description, icon_url, status_color}.
    """
    raw = safe_call(jira.get, "rest/api/2/priority")
    items = raw if isinstance(raw, list) else []
    return sanitize_strings(envelope_full([
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "description": p.get("description"),
            "icon_url": p.get("iconUrl"),
            "status_color": p.get("statusColor"),
        }
        for p in items
    ]))


def jira_list_resolutions() -> dict:
    """List all resolutions defined in the Jira instance.

    Returns list of {id, name, description}.
    """
    raw = safe_call(jira.get, "rest/api/2/resolution")
    items = raw if isinstance(raw, list) else []
    return sanitize_strings(envelope_full([
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "description": r.get("description"),
        }
        for r in items
    ]))


def jira_list_link_types() -> dict:
    """List all issue link types defined in the Jira instance.

    Useful for picking the right `link_type` for jira_add_link — the exact
    string Jira expects ('Blocks', 'Relates', 'Cloners', etc.).

    Returns list of {id, name, inward, outward}.
    """
    raw = safe_call(jira.get, "rest/api/2/issueLinkType")
    items: list = []
    if isinstance(raw, dict):
        items = raw.get("issueLinkTypes") or []
    elif isinstance(raw, list):
        items = raw
    return sanitize_strings(envelope_full([
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "inward": t.get("inward"),
            "outward": t.get("outward"),
        }
        for t in items
    ]))


# --------- create metadata ---------

def jira_get_create_meta(
    project_key: str,
    issue_type: str | None = None,
) -> dict:
    """Get create-meta for a Jira project: which fields are required/optional
    when creating an issue, what allowed values they have.

    Critical for creating issues in unfamiliar projects — without this you
    don't know if a project demands custom fields, or what the customfield_*
    IDs are for fields like 'Product', 'Stage', etc.

    Args:
        project_key: project key.
        issue_type: optionally narrow to a single issue type name.
                    If omitted, returns metadata for all issue types in the project.

    Returns the raw createmeta payload as Atlassian sends it, with `expand=
    projects.issuetypes.fields`. Each field has type info and allowedValues
    when applicable. Caller should use the customfield_XXXXX IDs verbatim
    when passing custom_fields to jira_create_issue / jira_update_issue.

    Shape:
        {
          "project_key": "PP",
          "issue_types": [
            {
              "name": "Task", "id": "10001",
              "fields": {
                "summary": {"required": true, "name": "Summary", "schema": {...}},
                "customfield_10101": {"required": false, "name": "Product",
                                      "allowedValues": [{"id": "1", "value": "FX"}, ...]},
                ...
              }
            }, ...
          ]
        }
    """
    params = {
        "projectKeys": project_key.upper(),
        "expand": "projects.issuetypes.fields",
    }
    if issue_type:
        params["issuetypeNames"] = issue_type

    raw = safe_call(jira.get, "rest/api/2/issue/createmeta", params=params)
    projects = raw.get("projects") if isinstance(raw, dict) else None
    if not projects:
        raise ToolError(
            f"No createmeta returned for project '{project_key}'. "
            "Check the project key and that the bot has Browse Projects + "
            "Create Issues permissions."
        )

    # We asked for a single project, take it
    proj = projects[0]
    issue_types_raw = proj.get("issuetypes") or []

    issue_types = []
    for it in issue_types_raw:
        # Pass through fields dict mostly as-is; just trim a few obvious noise keys
        fields = {}
        for fid, fdef in (it.get("fields") or {}).items():
            field_clean = {
                "name": fdef.get("name"),
                "required": fdef.get("required", False),
                "schema": fdef.get("schema"),
                "operations": fdef.get("operations"),
                "auto_complete_url": fdef.get("autoCompleteUrl"),
            }
            if "allowedValues" in fdef:
                # Compress allowedValues — keep only id/value/name to avoid
                # dragging entire user/version/component objects across MCP
                av = []
                for v in fdef["allowedValues"]:
                    if isinstance(v, dict):
                        av.append({
                            "id": v.get("id"),
                            "value": v.get("value") or v.get("name"),
                            "name": v.get("name"),
                        })
                    else:
                        av.append(v)
                field_clean["allowed_values"] = av
            fields[fid] = field_clean

        issue_types.append({
            "id": it.get("id"),
            "name": it.get("name"),
            "description": it.get("description"),
            "subtask": it.get("subtask", False),
            "fields": fields,
        })

    return sanitize_strings({
        "project_key": proj.get("key"),
        "project_id": proj.get("id"),
        "project_name": proj.get("name"),
        "issue_types": issue_types,
    })


# --------- fields ---------

def jira_list_fields() -> dict:
    """List all Jira fields, including custom fields.

    Essential for translating human field names ('Product', 'Stage', 'Sprint')
    into customfield_XXXXX IDs that the API accepts.

    Returns list of {id, key, name, custom, type, searchable, items_type}.
    """
    raw = safe_call(jira.get, "rest/api/2/field")
    items = raw if isinstance(raw, list) else []
    out = []
    for f in items:
        schema = f.get("schema") or {}
        out.append({
            "id": f.get("id"),
            "key": f.get("key"),
            "name": f.get("name"),
            "custom": f.get("custom", False),
            "type": schema.get("type"),
            "items_type": schema.get("items"),
            "custom_id": schema.get("customId"),
            "searchable": f.get("searchable", False),
        })
    return sanitize_strings(envelope_full(out))


# --------- groups ---------

def jira_list_groups(query: str | None = None, max_results: int = 50) -> dict:
    """List Jira groups (used for assignee matrices, filter share permissions, etc.).

    Args:
        query: optional substring to filter group names.
        max_results: cap on results (default 50, up to 100).

    Returns list of {name, html_name}.
    """
    params: dict[str, Any] = {"maxResults": min(max_results, 100)}
    if query:
        params["query"] = query

    raw = safe_call(jira.get, "rest/api/2/groups/picker", params=params)
    groups = raw.get("groups") if isinstance(raw, dict) else None
    items = groups or []
    return sanitize_strings(envelope_full([
        {
            "name": g.get("name"),
            "html_name": g.get("html"),
        }
        for g in items
    ]))


def jira_list_group_members(
    group_name: str,
    include_inactive: bool = False,
    max_results: int = 50,
    start_at: int = 0,
) -> dict:
    """List members of a Jira group, paginated.

    Args:
        group_name: exact group name (use jira_list_groups to discover).
        include_inactive: include disabled users (default False).
        max_results: page size (default 50, up to 100 per Jira).
        start_at: pagination offset.

    Returns {results, pagination}.
    """
    params = {
        "groupname": group_name,
        "includeInactiveUsers": str(include_inactive).lower(),
        "maxResults": min(max_results, 100),
        "startAt": start_at,
    }
    try:
        raw = jira.get("rest/api/2/group/member", params=params)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "Administrator privileges" in msg \
                or "not authorized" in msg.lower():
            raise ToolError(
                f"Listing members of '{group_name}' requires elevated permissions. "
                f"On most Jira DC instances this needs 'Browse Users' global "
                f"permission AND, for some groups, JIRA Administrators. "
                f"Original error: {type(e).__name__}: {e}"
            ) from e
        raise ToolError(f"{type(e).__name__}: {e}") from e

    if not isinstance(raw, dict):
        raise ToolError(f"Unexpected response from group/member: {raw}")

    values = raw.get("values") or []
    is_last = raw.get("isLast", False)
    total = raw.get("total")  # DC may or may not return total here
    returned = len(values)

    members = [
        {
            "name": u.get("name"),
            "key": u.get("key"),
            "email": u.get("emailAddress"),
            "displayName": u.get("displayName"),
            "active": u.get("active"),
        }
        for u in values
    ]

    next_start = None if is_last else start_at + returned

    return sanitize_strings({
        "results": members,
        "pagination": {
            "start_at": start_at,
            "max_results": min(max_results, 100),
            "size": returned,
            "total": total,
            "is_last": is_last,
            "next_start_at": next_start,
        },
    })


TOOLS = [
    jira_list_projects,
    jira_get_project,
    jira_list_issue_types,
    jira_list_statuses,
    jira_list_priorities,
    jira_list_resolutions,
    jira_list_link_types,
    jira_get_create_meta,
    jira_list_fields,
    jira_list_groups,
    jira_list_group_members,
]
