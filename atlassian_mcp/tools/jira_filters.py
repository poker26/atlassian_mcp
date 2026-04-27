"""Jira saved filters and their share permissions.

Wraps /rest/api/2/filter on Jira Data Center.

DC quirk: this Jira instance returns 404 for /filter/my and /filter/search.
The only reliable per-user enumeration endpoint is /filter/favourite.
That means jira_list_my_filters and the if_exists lookup in jira_create_filter
operate strictly on the bot's FAVOURITE filters — fillters created without
favourite=True are invisible to those operations.

Recommendation: create filters with favourite=True (the default here) so
later runs can find them again.
"""
from __future__ import annotations

from typing import Any

from atlassian_mcp.clients import jira
from atlassian_mcp.tools.common import (
    ToolError,
    envelope_full,
    safe_call,
    sanitize_strings,
)


def _base() -> str:
    return jira.url.rstrip("/")


def _filter_url(filter_id: str | int) -> str:
    return f"{_base()}/issues/?filter={filter_id}"


def _shape_filter(f: dict) -> dict:
    """Normalize a Jira filter response into the MCP shape we return."""
    owner = f.get("owner") or {}
    fid = f.get("id")
    return {
        "id": fid,
        "name": f.get("name"),
        "description": f.get("description"),
        "jql": f.get("jql"),
        "owner": owner.get("displayName") or owner.get("name"),
        "owner_username": owner.get("name"),
        "favourite": f.get("favourite", False),
        "share_permissions": f.get("sharePermissions") or [],
        "view_url": _filter_url(fid) if fid else None,
        "search_url": f.get("searchUrl"),
    }


# --------- read ---------

def jira_get_filter(filter_id: str) -> dict:
    """Get a single Jira filter by id.

    Args:
        filter_id: numeric filter id as string.
    """
    data = safe_call(
        jira.get,
        f"rest/api/2/filter/{filter_id}",
        params={"expand": "sharePermissions"},
    )
    return sanitize_strings(_shape_filter(data))


def jira_list_my_filters() -> dict:
    """List filters in the bot's favourites (i.e. the 'starred' list).

    DC limitation: this instance does not expose /filter/my or /filter/search,
    so this tool can only see filters the bot has favourited. New filters
    created with jira_create_filter(favourite=True) automatically appear here.

    Returns {results, pagination}.
    """
    raw = safe_call(
        jira.get,
        "rest/api/2/filter/favourite",
        params={"expand": "sharePermissions"},
    )
    items = raw if isinstance(raw, list) else []
    return sanitize_strings(envelope_full([_shape_filter(f) for f in items]))


# --------- write ---------

def _find_favourite_filter_by_name(name: str) -> dict | None:
    """Look up a filter by exact name in the bot's favourites."""
    raw = safe_call(
        jira.get,
        "rest/api/2/filter/favourite",
        params={"expand": "sharePermissions"},
    )
    items = raw if isinstance(raw, list) else []
    for f in items:
        if (f.get("name") or "") == name:
            return f
    return None


def jira_create_filter(
    name: str,
    jql: str,
    description: str | None = None,
    favourite: bool = True,
    if_exists: str = "error",
) -> dict:
    """Create a new Jira filter.

    A freshly created filter is private (only visible to its owner).
    Call jira_set_filter_permissions afterwards to share it with a team.

    Args:
        name: filter name. Should be unique among the bot's filters.
        jql: JQL query saved in the filter.
        description: optional human description shown in Jira UI.
        favourite: if True (default), the filter is added to the bot's
                   favourites. Strongly recommended — it's the only way for
                   subsequent if_exists checks to find this filter on this
                   Jira DC instance.
        if_exists: collision strategy when a favourited filter with the
                   same name already exists:
                   - 'error' (default): raise ToolError.
                   - 'skip':  return the existing filter unchanged.
                   - 'update': overwrite jql/description on the existing one.
                   The lookup uses /filter/favourite, so it only sees
                   filters the bot has previously favourited. Filters the
                   bot created without favourite=True will not be found
                   and a duplicate may be created instead.

    Returns the created/updated/existing filter shape.
    """
    if if_exists not in ("error", "skip", "update"):
        raise ToolError("if_exists must be 'error', 'skip', or 'update'")

    existing = _find_favourite_filter_by_name(name)
    if existing is not None:
        if if_exists == "skip":
            return sanitize_strings(_shape_filter(existing))
        if if_exists == "update":
            return jira_update_filter(
                filter_id=str(existing.get("id")),
                jql=jql,
                description=description,
                favourite=favourite,
            )
        # error
        raise ToolError(
            f"Filter named '{name}' already exists in your favourites "
            f"(id={existing.get('id')}). "
            f"Use if_exists='skip' to keep it as-is, or "
            f"if_exists='update' to overwrite jql/description."
        )

    payload: dict[str, Any] = {"name": name, "jql": jql}
    if description is not None:
        payload["description"] = description
    if favourite:
        payload["favourite"] = True

    try:
        result = jira.post(
            "rest/api/2/filter",
            data=payload,
            params={"expand": "sharePermissions"},
        )
    except Exception as e:
        msg = str(e)
        # Jira's "name already in use" — pretty common, give a useful hint
        if ("already exists" in msg.lower() or
                "already in use" in msg.lower() or
                "name is taken" in msg.lower()):
            raise ToolError(
                f"A filter with the name '{name}' already exists for this "
                f"user, but it is not in the bot's favourites — so MCP "
                f"can't address it. Either choose a different name, or "
                f"favourite the existing filter manually in Jira UI then "
                f"re-run with if_exists='skip' or 'update'. "
                f"Original error: {type(e).__name__}: {e}"
            ) from e
        raise ToolError(f"{type(e).__name__}: {e}") from e

    if not isinstance(result, dict) or not result.get("id"):
        raise ToolError(f"Unexpected Jira create-filter response: {result}")
    return sanitize_strings(_shape_filter(result))


def jira_update_filter(
    filter_id: str,
    name: str | None = None,
    jql: str | None = None,
    description: str | None = None,
    favourite: bool | None = None,
) -> dict:
    """Update an existing Jira filter. Only non-None args are sent.

    Jira's PUT /filter/{id} requires the full payload (name + jql at minimum)
    even when only one field is being changed. We fetch existing and merge.
    """
    if not any(v is not None for v in (name, jql, description, favourite)):
        raise ToolError("No fields provided to update")

    existing = safe_call(jira.get, f"rest/api/2/filter/{filter_id}")
    payload: dict[str, Any] = {
        "name": name if name is not None else existing.get("name"),
        "jql": jql if jql is not None else existing.get("jql"),
    }
    new_desc = description if description is not None else existing.get("description")
    if new_desc is not None:
        payload["description"] = new_desc
    if favourite is not None:
        payload["favourite"] = favourite

    result = safe_call(
        jira.put,
        f"rest/api/2/filter/{filter_id}",
        data=payload,
        params={"expand": "sharePermissions"},
    )
    if not isinstance(result, dict):
        raise ToolError(f"Unexpected Jira update-filter response: {result}")
    return sanitize_strings(_shape_filter(result))


def jira_set_filter_permissions(
    filter_id: str,
    share_permissions: list[dict],
    replace: bool = True,
) -> dict:
    """Set share permissions on a Jira filter.

    Each entry in share_permissions is one of:
      {"type": "global"}                                — anyone (no login)
      {"type": "loggedin"}                              — anyone logged in
                                                          (alias for "authenticated")
      {"type": "project", "project_id": "<id>"}         — project members
      {"type": "project", "project_id": "<id>",
                          "role_id": "<role_id>"}       — project role only
      {"type": "group",   "group_name": "<group>"}      — group members
      {"type": "user",    "user_key":   "<JIRAUSER...>"} — single user
                                                          (use jira_search_users
                                                          to find the key)

    Args:
        filter_id: filter to modify.
        share_permissions: list of entries (see above).
        replace: when True (default), all existing share entries are deleted
                 first and the new ones added. When False, entries are added
                 to the existing set without removing anything.

    Returns the updated filter shape.
    """
    if not isinstance(share_permissions, list):
        raise ToolError("share_permissions must be a list of dicts")

    if replace:
        current = safe_call(
            jira.get,
            f"rest/api/2/filter/{filter_id}/permission",
        )
        existing_entries = current if isinstance(current, list) else []
        for e in existing_entries:
            eid = e.get("id")
            if eid is None:
                continue
            safe_call(
                jira.delete,
                f"rest/api/2/filter/{filter_id}/permission/{eid}",
            )

    # Jira DC SharePermissionInputBean wants lowercase `type` values, despite
    # the error message advertising UPPERCASE. Friendly aliases are mapped to
    # the wire-level lowercase names.
    type_alias = {
        "global": "global",
        "loggedin": "authenticated",        # UI calls this "Logged-in users"
        "authenticated": "authenticated",
        "project": "project",
        "project_role": "project_role",
        "group": "group",
        "user": "user",
    }

    # Jira treats GLOBAL and AUTHENTICATED ("loggedin") as exclusive —
    # neither can be combined with any other share entry. Catch this on
    # the MCP side so the caller gets a clear message instead of a raw
    # HTTP 400 from Jira.
    exclusive_types = {"global", "authenticated"}
    present_exclusive = [
        type_alias.get(str(e.get("type", "")).lower())
        for e in share_permissions
        if isinstance(e, dict)
        and type_alias.get(str(e.get("type", "")).lower()) in exclusive_types
    ]
    if present_exclusive and len(share_permissions) > 1:
        which = present_exclusive[0]
        friendly = "loggedin" if which == "authenticated" else which
        raise ToolError(
            f"Share type '{friendly}' cannot be combined with other share "
            f"entries — Jira treats it as a standalone, all-or-nothing grant. "
            f"Use it alone, or drop it and use group/user/project entries."
        )

    for entry in share_permissions:
        if not isinstance(entry, dict):
            raise ToolError(f"share_permissions entries must be dicts, got {entry!r}")

        raw_type = str(entry.get("type", "")).lower()
        share_type = type_alias.get(raw_type)
        if not share_type:
            raise ToolError(
                f"Unknown share type '{entry.get('type')}'. Must be one of: "
                f"{sorted(type_alias)}."
            )

        payload: dict[str, Any] = {"type": share_type}
        if share_type == "project":
            pid = entry.get("project_id")
            if not pid:
                raise ToolError("project share requires 'project_id'")
            payload["projectId"] = str(pid)
            if entry.get("role_id"):
                payload["projectRoleId"] = str(entry["role_id"])
                payload["type"] = "project_role"
        elif share_type == "project_role":
            pid = entry.get("project_id")
            rid = entry.get("role_id")
            if not pid or not rid:
                raise ToolError("project_role share requires 'project_id' and 'role_id'")
            payload["projectId"] = str(pid)
            payload["projectRoleId"] = str(rid)
        elif share_type == "group":
            gname = entry.get("group_name")
            if not gname:
                raise ToolError("group share requires 'group_name'")
            payload["groupname"] = gname
        elif share_type == "user":
            # On this DC the field is `userKey` (a JIRAUSER<n> id), not the
            # display username. Use jira_search_users to translate username
            # -> key first.
            ukey = entry.get("user_key") or entry.get("userKey")
            if not ukey:
                raise ToolError(
                    "user share requires 'user_key' (the JIRAUSER<n> id, "
                    "not the display username). Use jira_search_users to "
                    "look up a user's key by name."
                )
            payload["userKey"] = ukey
        # global / authenticated need no extra fields

        safe_call(
            jira.post,
            f"rest/api/2/filter/{filter_id}/permission",
            data=payload,
        )

    return jira_get_filter(filter_id)


TOOLS = [
    jira_get_filter,
    jira_list_my_filters,
    jira_create_filter,
    jira_update_filter,
    jira_set_filter_permissions,
]
