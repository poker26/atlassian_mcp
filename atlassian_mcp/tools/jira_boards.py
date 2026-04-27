"""Jira agile boards (Kanban / Scrum).

Wraps /rest/agile/1.0/board on Jira Software Data Center.

A board is always backed by a saved filter — boards have no JQL of their own.
The typical flow is:
  1. jira_create_filter(name=..., jql=..., favourite=True)
  2. jira_set_filter_permissions(filter_id, [{"type": "loggedin"}])
     (otherwise the board is only visible to the filter's owner)
  3. jira_create_board(name=..., type='kanban', filter_id=...)
"""
from __future__ import annotations

from typing import Any

from atlassian_mcp.clients import jira
from atlassian_mcp.tools.common import (
    ToolError,
    envelope_paginated,
    safe_call,
    sanitize_strings,
)


def _base() -> str:
    return jira.url.rstrip("/")


def _board_view_url(board_id: str | int, board_type: str | None) -> str:
    # The /secure/RapidBoard.jspa?rapidView=<id> URL works for both kanban and
    # scrum boards on DC, regardless of project context.
    return f"{_base()}/secure/RapidBoard.jspa?rapidView={board_id}"


def _shape_board(b: dict) -> dict:
    bid = b.get("id")
    btype = b.get("type")
    location = b.get("location") or {}
    return {
        "id": bid,
        "name": b.get("name"),
        "type": btype,
        "project_key": location.get("projectKey"),
        "project_name": location.get("projectName"),
        "view_url": _board_view_url(bid, btype) if bid else None,
    }


# --------- read ---------

def jira_list_boards(
    project_key: str | None = None,
    type: str | None = None,
    name: str | None = None,
    max_results: int = 25,
    start_at: int = 0,
) -> dict:
    """List Jira agile boards.

    Args:
        project_key: filter to boards scoped to this project (uses Jira's
                     projectKeyOrId param). Filtering works server-side, but
                     the project_key field in returned items may be null —
                     /board endpoint on Jira DC doesn't include `location` in
                     summary listings. Use jira_get_board_configuration on a
                     specific id to retrieve full location data.
        type: 'kanban' or 'scrum'.
        name: substring filter on board name (server-side via 'name' param).
        max_results: page size (default 25, up to 50 per Jira agile API).
        start_at: pagination offset.

    Returns {results, pagination}.
    """
    if type and type not in ("kanban", "scrum"):
        raise ToolError("type must be 'kanban' or 'scrum'")

    page_limit = min(max_results, 50)
    params: dict[str, Any] = {
        "startAt": start_at,
        "maxResults": page_limit,
    }
    if project_key:
        params["projectKeyOrId"] = project_key.upper()
    if type:
        params["type"] = type
    if name:
        params["name"] = name

    raw = safe_call(jira.get, "rest/agile/1.0/board", params=params)
    if not isinstance(raw, dict):
        raise ToolError(f"Unexpected response from /board: {raw}")

    values = raw.get("values") or []
    is_last = raw.get("isLast")
    total = raw.get("total")
    items = [_shape_board(b) for b in values]

    return sanitize_strings(envelope_paginated(
        items,
        start_at=start_at,
        limit=page_limit,
        total=total,
        is_last=is_last,
    ))


def jira_get_board_configuration(board_id: str) -> dict:
    """Get the configuration of a board: backing filter, columns, ranking, etc.

    Args:
        board_id: numeric board id.

    Returns {id, name, type, filter, columns, ranking, sub_query?, location?,
             estimation?, view_url}.
    """
    data = safe_call(
        jira.get,
        f"rest/agile/1.0/board/{board_id}/configuration",
    )
    if not isinstance(data, dict):
        raise ToolError(f"Unexpected response: {data}")

    f = data.get("filter") or {}
    columns = []
    for col in (data.get("columnConfig") or {}).get("columns", []) or []:
        columns.append({
            "name": col.get("name"),
            "min": col.get("min"),
            "max": col.get("max"),
            "statuses": [s.get("id") for s in (col.get("statuses") or [])],
        })

    ranking = data.get("ranking") or {}
    sub_query = data.get("subQuery") or {}
    location = data.get("location") or {}
    estimation = data.get("estimation") or {}

    btype = data.get("type")
    bid = data.get("id")
    return sanitize_strings({
        "id": bid,
        "name": data.get("name"),
        "type": btype,
        "filter": {
            "id": f.get("id"),
            "self": f.get("self"),
        },
        "columns": columns,
        "ranking": {"rank_custom_field_id": ranking.get("rankCustomFieldId")},
        "sub_query": sub_query.get("query"),
        "location": {
            "type": location.get("type"),
            "key": location.get("key"),
            "id": location.get("id"),
            "name": location.get("name"),
        } if location else None,
        "estimation": {
            "type": estimation.get("type"),
            "field_id": (estimation.get("field") or {}).get("fieldId"),
        } if estimation else None,
        "view_url": _board_view_url(bid, btype) if bid else None,
    })


# --------- write ---------

def jira_create_board(
    name: str,
    type: str,
    filter_id: str,
) -> dict:
    """Create a new Jira agile board backed by a saved filter.

    The backing filter must exist (use jira_create_filter first) and must be
    shared appropriately — a board can only be seen by users who have view
    permission on its filter.

    Args:
        name: board name.
        type: 'kanban' or 'scrum'.
        filter_id: id of the saved filter that backs the board.

    Returns the same shape as jira_get_board_configuration for the new board,
    so the caller immediately has the filter linkage and view_url.

    Note on board location: the `location` field in BoardCreateBean isn't
    accepted by this Jira DC build (rejected with "Unrecognized field"
    HTTP 500). The board is therefore created without an explicit project
    scope. To attach it to a project after creation, edit Board configuration
    → Location through the Jira UI.
    """
    if type not in ("kanban", "scrum"):
        raise ToolError("type must be 'kanban' or 'scrum'")

    payload: dict[str, Any] = {
        "name": name,
        "type": type,
        "filterId": int(filter_id) if str(filter_id).isdigit() else filter_id,
    }

    result = safe_call(jira.post, "rest/agile/1.0/board", data=payload)
    if not isinstance(result, dict) or not result.get("id"):
        raise ToolError(f"Unexpected create-board response: {result}")

    # POST /board doesn't echo the full configuration; fetch it so the caller
    # gets a complete shape with filter/columns/etc.
    return jira_get_board_configuration(str(result["id"]))


def jira_update_board_filter(board_id: str, filter_id: str) -> dict:
    """Repoint an existing board at a different saved filter.

    There's no native "PATCH board" endpoint; the supported way is to update
    the filter that the board is currently bound to. To actually re-bind the
    board to a *different* filter id, Jira DC requires going through the
    Configuration UI — the REST API has no direct call. As a programmatic
    workaround we update the *current* backing filter to use the new filter's
    JQL. This effectively swaps content without changing the board id.

    Args:
        board_id: target board.
        filter_id: id of the filter whose JQL should be applied to the
                   board's backing filter.

    Returns the updated board configuration shape.

    NOTE: this rewrites the JQL of the board's existing filter. If that
    filter is shared with other boards or users, those will also see the new
    JQL. For a clean re-bind, recreate the board with jira_create_board
    pointing at the new filter and delete the old board through UI.
    """
    # Fetch current board config to find its backing filter id
    cfg = jira_get_board_configuration(board_id)
    current_filter_id = (cfg.get("filter") or {}).get("id")
    if not current_filter_id:
        raise ToolError(
            f"Board {board_id} has no backing filter id in its configuration"
        )

    # Fetch the new filter's JQL
    new_filter = safe_call(jira.get, f"rest/api/2/filter/{filter_id}")
    new_jql = new_filter.get("jql")
    new_name = new_filter.get("name")
    if not new_jql:
        raise ToolError(f"Filter {filter_id} has no JQL")

    # Read the current backing filter to keep its name/description
    current_filter = safe_call(
        jira.get, f"rest/api/2/filter/{current_filter_id}",
    )
    payload = {
        "name": current_filter.get("name") or new_name,
        "jql": new_jql,
    }
    if current_filter.get("description") is not None:
        payload["description"] = current_filter["description"]

    safe_call(
        jira.put,
        f"rest/api/2/filter/{current_filter_id}",
        data=payload,
    )

    # Return the refreshed board config
    return jira_get_board_configuration(board_id)


TOOLS = [
    jira_list_boards,
    jira_get_board_configuration,
    jira_create_board,
    jira_update_board_filter,
]
