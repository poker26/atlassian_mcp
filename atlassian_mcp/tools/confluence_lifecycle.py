"""Confluence page lifecycle operations beyond create/update.

This module currently exposes only `confluence_move_page`. Two related
operations were investigated but proved unsupported on Confluence DC 7.19
and are intentionally NOT implemented here:

  - archive_page: PUT /content/{id} silently ignores the `status` field
    on this DC build (the response always shows status='current'). There is
    no working REST endpoint for soft-archive on DC 7.19 — use the UI
    (Page tools → Archive).

  - delete_page: per project convention, no destructive REST tools.
    Use the Confluence UI to send a page to trash.

Move semantics: PUT /rest/api/content/{id} with `ancestors` accepts a new
parent. Confluence returns the entire ancestor path (root → ... → direct
parent). The direct parent is always the last element.
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


def confluence_move_page(
    page_id: str,
    target_parent_id: str,
) -> dict:
    """Move a Confluence page under a different parent (in the same space).

    Cross-space moves are NOT supported by this tool — PUT with ancestors
    only re-parents within the same space. To move a page to another space,
    use confluence_copy_page (which clones into a target space) and then
    delete the original through the UI.

    Args:
        page_id: page to move.
        target_parent_id: new direct parent page id. Must be in the same space.

    Returns:
        {
          "id": "...",
          "title": "...",
          "version": <new>,
          "url": "...",
          "space_key": "...",
          "direct_parent_id": "<target_parent_id>",
          "ancestors_path": [{"id": "...", "title": "..."}, ...]
        }

    The `ancestors_path` is what Confluence echoes back — the full chain
    from space root to the new direct parent. The last element should match
    `target_parent_id`. If it does not, the move was tacitly ignored by the
    server (the tool raises ToolError to surface that).
    """
    if str(page_id) == str(target_parent_id):
        raise ToolError(
            f"Cannot move page {page_id} under itself — that would create "
            f"a cycle in the page tree."
        )

    # Fetch current state — we need version+1 and the title (PUT requires
    # the title in the payload even when not changing it).
    current = safe_call(
        confluence.get,
        f"rest/api/content/{page_id}",
        params={"expand": "version,space,ancestors"},
    )
    if not isinstance(current, dict):
        raise ToolError(f"Unexpected response fetching page {page_id}: {current}")

    title = current.get("title")
    if not title:
        raise ToolError(f"Page {page_id} has no title — cannot construct PUT payload")

    cur_version = (current.get("version") or {}).get("number")
    if not isinstance(cur_version, int):
        raise ToolError(
            f"Page {page_id} has no numeric version (got {cur_version!r})"
        )
    next_version = cur_version + 1

    space_key = (current.get("space") or {}).get("key")

    payload: dict[str, Any] = {
        "version": {"number": next_version},
        "type": "page",
        "title": title,
        "ancestors": [{"id": str(target_parent_id)}],
    }

    result = safe_call(
        confluence.put,
        f"rest/api/content/{page_id}",
        data=payload,
    )
    if not isinstance(result, dict):
        raise ToolError(f"Unexpected response from PUT: {result}")

    new_ancestors = result.get("ancestors") or []
    direct_parent = new_ancestors[-1].get("id") if new_ancestors else None

    if str(direct_parent) != str(target_parent_id):
        # Confluence accepted the request but didn't actually re-parent.
        # We've seen this on this DC for `status` field; defending against it.
        raise ToolError(
            f"Move appeared to succeed (HTTP 200) but the new direct parent "
            f"is {direct_parent!r}, not the requested {target_parent_id!r}. "
            f"The server may have silently rejected the ancestors change. "
            f"Check page permissions and that target parent is in the same space."
        )

    return sanitize_strings({
        "id": result.get("id"),
        "title": result.get("title"),
        "version": (result.get("version") or {}).get("number"),
        "url": f"{_base()}/pages/viewpage.action?pageId={result.get('id')}",
        "space_key": (result.get("space") or {}).get("key", space_key),
        "direct_parent_id": direct_parent,
        "ancestors_path": [
            {"id": a.get("id"), "title": a.get("title")}
            for a in new_ancestors
        ],
    })


TOOLS = [
    confluence_move_page,
]
