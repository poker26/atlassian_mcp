"""Confluence page-level read/edit restrictions.

Wraps the restriction endpoints on Confluence Data Center 7.19. This DC
build splits the API across two paths:

  read  → GET    /rest/api/content/{id}/restriction/byOperation
  write → PUT    /rest/experimental/content/{id}/restriction
  clear → DELETE /rest/experimental/content/{id}/restriction

We had to mix paths because:
- /rest/api/.../restriction       only allows OPTIONS (no GET/PUT/DELETE)
- /rest/api/.../restriction/byOperation/{op}  is read-only (Allow: GET,HEAD,OPTIONS)
- /rest/experimental/.../restriction      allows GET/PUT/DELETE/POST, but its
                                           GET silently returns empty
                                           restrictions even when set
- /rest/api/.../restriction/byOperation    GET works correctly

Restrictions concept: each page has up to two operation buckets — `read`
and `update`. If a bucket has no entries, the operation falls through to
space-level permissions. If it has entries (users or groups), only those
principals can perform that operation.

User identifiers: this DC expects userKey (the JIRAUSER<n>-style hex id),
not the human-readable username. Use confluence_get_user(by='username') to
translate first when starting from a name.
"""
from __future__ import annotations

from typing import Any

from atlassian_mcp.clients import confluence
from atlassian_mcp.tools.common import (
    ToolError,
    safe_call,
    sanitize_strings,
)


def _base() -> str:
    return confluence.url.rstrip("/")


def _shape_user(u: dict) -> dict:
    return {
        "userKey": u.get("userKey"),
        "username": u.get("username"),
        "displayName": u.get("displayName"),
    }


def _shape_group(g: dict) -> dict:
    return {"name": g.get("name")}


# --------- read ---------

def confluence_get_page_restrictions(page_id: str) -> dict:
    """Get the current view/edit restrictions on a Confluence page.

    Args:
        page_id: target page id.

    Returns:
        {
          "page_id": "...",
          "url": "...",
          "view": {"users": [...], "groups": [...]},
          "edit": {"users": [...], "groups": [...]}
        }

    Empty arrays mean "fall through to space permissions" — there are NO
    page-level restrictions for that operation.
    """
    raw = safe_call(
        confluence.get,
        f"rest/api/content/{page_id}/restriction/byOperation",
        params={
            "expand": (
                "read.restrictions.user,read.restrictions.group,"
                "update.restrictions.user,update.restrictions.group"
            ),
        },
    )
    if not isinstance(raw, dict):
        raise ToolError(f"Unexpected restriction response: {raw}")

    def _bucket(op_name: str) -> dict:
        op = raw.get(op_name) or {}
        r = op.get("restrictions") or {}
        users = (r.get("user") or {}).get("results") or []
        groups = (r.get("group") or {}).get("results") or []
        return {
            "users": [_shape_user(u) for u in users],
            "groups": [_shape_group(g) for g in groups],
        }

    return sanitize_strings({
        "page_id": page_id,
        "url": f"{_base()}/pages/viewpage.action?pageId={page_id}",
        "view": _bucket("read"),
        "edit": _bucket("update"),
    })


# --------- write ---------

def _build_principals(
    users: list | None,
    groups: list | None,
) -> dict:
    """Translate friendly inputs into the API's user/group entry lists."""
    user_entries = []
    for u in users or []:
        if isinstance(u, dict):
            ukey = u.get("userKey") or u.get("user_key")
            if not ukey:
                raise ToolError(
                    f"User entry must have userKey/user_key, got {u!r}. "
                    "Use confluence_get_user(by='username') to find the key."
                )
            user_entries.append({"type": "known", "userKey": ukey})
        elif isinstance(u, str):
            # Accept bare userKey strings for convenience
            user_entries.append({"type": "known", "userKey": u})
        else:
            raise ToolError(f"User entries must be dict or str, got {u!r}")

    group_entries = []
    for g in groups or []:
        if isinstance(g, dict):
            name = g.get("name") or g.get("group_name")
            if not name:
                raise ToolError(
                    f"Group entry must have name/group_name, got {g!r}"
                )
            group_entries.append({"type": "group", "name": name})
        elif isinstance(g, str):
            group_entries.append({"type": "group", "name": g})
        else:
            raise ToolError(f"Group entries must be dict or str, got {g!r}")

    return {"user": user_entries, "group": group_entries}


def confluence_set_page_restrictions(
    page_id: str,
    view_users: list | None = None,
    view_groups: list | None = None,
    edit_users: list | None = None,
    edit_groups: list | None = None,
) -> dict:
    """Set view/edit restrictions on a Confluence page.

    REPLACE semantics: the lists you pass for each operation entirely
    replace the existing entries for that operation. To clear an operation
    bucket pass an empty list. To leave an operation untouched, omit it
    (pass None / don't pass the argument).

    Note: the underlying Confluence DC API takes a full payload covering
    BOTH operations at once. To support "leave update alone" semantics, we
    fetch current state first and merge — only the operations whose lists
    you provided are replaced; the others are passed back unchanged.

    Args:
        page_id: target page id.
        view_users:   list of users that may VIEW the page.
                      Each entry can be a userKey string, or a dict with
                      'userKey'/'user_key' (and optionally other fields,
                      which are ignored). Pass [] to clear, None to leave alone.
        view_groups:  list of groups that may VIEW the page.
                      Each entry: group name string, or dict with 'name'.
        edit_users:   list of users that may EDIT the page (same format).
        edit_groups:  list of groups that may EDIT the page.

    Returns the new restrictions state in the same shape as
    confluence_get_page_restrictions.
    """
    if all(v is None for v in (view_users, view_groups, edit_users, edit_groups)):
        raise ToolError(
            "Pass at least one of view_users/view_groups/edit_users/edit_groups. "
            "To clear all restrictions, use confluence_remove_page_restrictions."
        )

    # Fetch current state so we can preserve any bucket the caller didn't touch
    current = confluence_get_page_restrictions(page_id)

    def _resolved_users(provided: list | None, current_users: list[dict]) -> list:
        if provided is None:
            # Pass current users back as userKey list
            return [u["userKey"] for u in current_users if u.get("userKey")]
        return provided

    def _resolved_groups(provided: list | None, current_groups: list[dict]) -> list:
        if provided is None:
            return [g["name"] for g in current_groups if g.get("name")]
        return provided

    read_principals = _build_principals(
        _resolved_users(view_users, current["view"]["users"]),
        _resolved_groups(view_groups, current["view"]["groups"]),
    )
    update_principals = _build_principals(
        _resolved_users(edit_users, current["edit"]["users"]),
        _resolved_groups(edit_groups, current["edit"]["groups"]),
    )

    payload = [
        {
            "operation": "read",
            "restrictions": read_principals,
        },
        {
            "operation": "update",
            "restrictions": update_principals,
        },
    ]

    safe_call(
        confluence.put,
        f"rest/experimental/content/{page_id}/restriction",
        data=payload,
    )

    # Read back through the path we know returns truth
    return confluence_get_page_restrictions(page_id)


def confluence_remove_page_restrictions(page_id: str) -> dict:
    """Remove ALL view and edit restrictions from a Confluence page.

    After this call the page falls through to space-level permissions for
    both `read` and `update` operations — i.e. anyone with space access can
    see and edit it.

    Args:
        page_id: target page id.

    Returns the resulting (empty) restrictions state, same shape as
    confluence_get_page_restrictions. `view` and `edit` will both have
    empty `users` and `groups` lists if removal succeeded.
    """
    safe_call(
        confluence.delete,
        f"rest/experimental/content/{page_id}/restriction",
    )
    return confluence_get_page_restrictions(page_id)


TOOLS = [
    confluence_get_page_restrictions,
    confluence_set_page_restrictions,
    confluence_remove_page_restrictions,
]
