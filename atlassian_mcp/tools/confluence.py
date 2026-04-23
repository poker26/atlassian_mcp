"""Confluence tools (REST API v1 via atlassian-python-api, Confluence 7.19 DC)."""
from __future__ import annotations

from typing import Any

from atlassian_mcp.clients import confluence
from atlassian_mcp.tools.common import (
    ToolError,
    b64decode_to_bytes,
    b64encode_bytes,
    safe_call,
    to_storage,
)


def _base() -> str:
    return confluence.url.rstrip("/")


# --------- read ---------

def confluence_list_spaces(limit: int = 25, start: int = 0) -> list[dict]:
    """List Confluence spaces visible to the authenticated user.

    Args:
        limit: how many spaces to return (default 25, up to 100).
        start: pagination offset (default 0).

    Returns list of {key, name, type, url}.
    """
    raw = safe_call(confluence.get_all_spaces, start=start, limit=min(limit, 100))
    results = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
    base = _base()
    return [
        {
            "key": s.get("key"),
            "name": s.get("name"),
            "type": s.get("type"),
            "url": f"{base}/display/{s.get('key')}" if s.get("key") else None,
        }
        for s in results
    ]


def confluence_get_page(page_id: str, include_body: bool = True) -> dict:
    """Get a Confluence page by id: title, space, version, body (storage format), URL.

    Args:
        page_id: numeric page id as string.
        include_body: whether to include storage-format body (default True).
    """
    expand = "space,version,body.storage" if include_body else "space,version"
    page = safe_call(confluence.get_page_by_id, page_id, expand=expand)
    return {
        "id": page.get("id"),
        "title": page.get("title"),
        "space_key": (page.get("space") or {}).get("key"),
        "space_name": (page.get("space") or {}).get("name"),
        "version": (page.get("version") or {}).get("number"),
        "url": f"{_base()}/pages/viewpage.action?pageId={page.get('id')}",
        "body_storage": ((page.get("body") or {}).get("storage") or {}).get("value") if include_body else None,
    }


def confluence_search_by_title(space_key: str, title: str, limit: int = 10) -> list[dict]:
    """Find a Confluence page in a space by exact title match.

    Args:
        space_key: space key (e.g. 'FF', 'IT').
        title: page title (exact match).
        limit: max results (unused; API returns 0 or 1).

    Returns list of {id, title, space_key, url}.
    """
    raw = safe_call(confluence.get_page_by_title, space_key, title)
    if not raw:
        return []
    return [{
        "id": raw.get("id"),
        "title": raw.get("title"),
        "space_key": (raw.get("space") or {}).get("key", space_key),
        "url": f"{_base()}/pages/viewpage.action?pageId={raw.get('id')}",
    }]


def confluence_search_cql(cql: str, limit: int = 25) -> list[dict]:
    """Full-text search over Confluence using CQL.

    Examples:
      - `type = page AND space = FF AND text ~ "MVP"`
      - `title = "Release notes" AND lastModified >= "2026/01/01"`
      - `label = "onboarding"`

    Args:
        cql: Confluence Query Language expression.
        limit: max results (up to 50).

    Returns list of {id, type, title, space_key, url, excerpt}.
    """
    raw = safe_call(
        confluence.cql,
        cql,
        limit=min(limit, 50),
        expand="content.space",  # чтобы space_key приходил в ответе
    )
    results = raw.get("results", []) if isinstance(raw, dict) else []
    base = _base()
    out = []
    for r in results:
        content = r.get("content", {}) or {}
        cid = content.get("id")
        space = content.get("space") or {}
        out.append({
            "id": cid,
            "type": content.get("type"),
            "title": content.get("title"),
            "space_key": space.get("key"),
            "url": f"{base}/pages/viewpage.action?pageId={cid}" if cid else None,
            "excerpt": r.get("excerpt"),
        })
    return out


def confluence_get_page_children(page_id: str, limit: int = 50, start: int = 0) -> list[dict]:
    """List direct child pages of a Confluence page.

    Args:
        page_id: parent page id.
        limit: max children (default 50, up to 200).
        start: pagination offset.

    Returns list of {id, title, url}.
    """
    raw = safe_call(
        confluence.get_page_child_by_type,
        page_id,
        type="page",
        start=start,
        limit=min(limit, 200),
    )
    results = raw if isinstance(raw, list) else (raw.get("results", []) if isinstance(raw, dict) else [])
    base = _base()
    return [
        {
            "id": p.get("id"),
            "title": p.get("title"),
            "url": f"{base}/pages/viewpage.action?pageId={p.get('id')}",
        }
        for p in results
    ]


def confluence_get_page_history(page_id: str, limit: int = 25) -> list[dict]:
    """Version history of a Confluence page.

    Args:
        page_id: page id.
        limit: max versions (default 25, up to 100).

    Returns list of {version, when, by, message, minor}.
    """
    raw = safe_call(
        confluence.get,
        f"rest/experimental/content/{page_id}/version",
        params={"limit": min(limit, 100)},
    )
    results = raw.get("results", []) if isinstance(raw, dict) else []
    out = []
    for v in results:
        by = v.get("by") or {}
        out.append({
            "version": v.get("number"),
            "when": v.get("when"),
            "by": by.get("displayName") or by.get("username"),
            "message": v.get("message") or "",
            "minor": v.get("minorEdit", False),
        })
    return out


# --------- write ---------

def confluence_create_page(
    space_key: str,
    title: str,
    content: str,
    content_format: str = "storage",
    parent_id: str | None = None,
) -> dict:
    """Create a new Confluence page.

    Args:
        space_key: target space key.
        title: page title (must be unique within the space).
        content: page body.
        content_format: one of 'storage', 'wiki', 'plain', 'markdown' (default 'storage').
        parent_id: optional parent page id. If omitted, page is created at space root.

    Returns {id, title, version, url}.
    """
    body_value, representation = to_storage(content, content_format)
    result = safe_call(
        confluence.create_page,
        space=space_key,
        title=title,
        body=body_value,
        parent_id=parent_id,
        representation=representation,
    )
    page_id = result.get("id")
    if not page_id:
        raise ToolError(f"Unexpected Confluence response: {result}")
    return {
        "id": page_id,
        "title": result.get("title"),
        "version": (result.get("version") or {}).get("number", 1),
        "url": f"{_base()}/pages/viewpage.action?pageId={page_id}",
    }


def confluence_update_page(
    page_id: str,
    content: str,
    title: str | None = None,
    content_format: str = "storage",
    minor_edit: bool = False,
) -> dict:
    """Update an existing Confluence page. Version is auto-incremented.

    Args:
        page_id: page id to update.
        content: new body content.
        title: optional new title; keeps current title if omitted.
        content_format: one of 'storage', 'wiki', 'plain', 'markdown'.
        minor_edit: if True, doesn't notify watchers.

    Returns {id, title, version, url}.
    """
    body_value, representation = to_storage(content, content_format)

    existing = safe_call(
        confluence.get_page_by_id,
        page_id,
        expand="version,space",
    )
    new_title = title or existing.get("title")

    result = safe_call(
        confluence.update_page,
        page_id=page_id,
        title=new_title,
        body=body_value,
        representation=representation,
        minor_edit=minor_edit,
    )
    return {
        "id": result.get("id"),
        "title": result.get("title"),
        "version": (result.get("version") or {}).get("number"),
        "url": f"{_base()}/pages/viewpage.action?pageId={result.get('id')}",
    }


def confluence_move_page(
    page_id: str,
    target_parent_id: str | None = None,
    target_space_key: str | None = None,
) -> dict:
    """Move a Confluence page. NOT SUPPORTED on this Confluence DC instance.

    Confluence 7.19 at this deployment does not expose a REST endpoint that
    actually moves pages. The `/rest/api/content/{id}/move/{position}/{target}`
    endpoint returns 404, and updating `ancestors` via PUT is silently ignored
    by the server (version increments but parent stays unchanged).

    To move pages, use the Confluence web UI (Page tools -> Move).

    As a lossy workaround, you can: read the page with `confluence_get_page`,
    create a copy under the target parent with `confluence_create_page`, then
    delete the original through the UI. This loses version history, comments,
    attachments, watchers, labels, and breaks any incoming links.
    """
    raise ToolError(
        "confluence_move_page: moving pages is not supported via REST on this "
        "Confluence DC 7.19 instance. Use the Confluence UI to move pages. "
        "Alternative (lossy): confluence_get_page + confluence_create_page "
        "under the new parent, then delete the original manually."
    )




# --------- comments ---------

def confluence_get_page_comments(
    page_id: str,
    limit: int = 25,
    start: int = 0,
    location: str = "footer",
) -> list[dict]:
    """Get comments attached to a Confluence page.

    Args:
        page_id: page id.
        limit: max comments (default 25, up to 100).
        start: pagination offset.
        location: 'footer' (page bottom comments) or 'inline' (highlight comments).
                  Default 'footer'. Pass 'all' to include both.

    Returns list of {id, author, created, body_storage, location}.
    """
    if location not in ("footer", "inline", "all"):
        raise ToolError("location must be 'footer', 'inline', or 'all'")

    locations = ["footer", "inline"] if location == "all" else [location]

    out: list[dict] = []
    for loc in locations:
        raw = safe_call(
            confluence.get,
            f"rest/api/content/{page_id}/child/comment",
            params={
                "location": loc,
                "expand": "body.storage,version,history.createdBy",
                "limit": min(limit, 100),
                "start": start,
            },
        )
        results = raw.get("results", []) if isinstance(raw, dict) else []
        for c in results:
            history = c.get("history") or {}
            created_by = history.get("createdBy") or {}
            out.append({
                "id": c.get("id"),
                "author": created_by.get("displayName") or created_by.get("username"),
                "created": history.get("createdDate"),
                "body_storage": ((c.get("body") or {}).get("storage") or {}).get("value", ""),
                "location": loc,
            })
    return out


def confluence_add_comment(
    page_id: str,
    comment: str,
    content_format: str = "plain",
) -> dict:
    """Add a footer comment to a Confluence page.

    Args:
        page_id: page id to comment on.
        comment: comment body.
        content_format: one of 'storage', 'wiki', 'plain', 'markdown' (default 'plain').

    Returns {id, author, created, url}.
    """
    body_value, representation = to_storage(comment, content_format)

    payload = {
        "type": "comment",
        "container": {"id": page_id, "type": "page"},
        "body": {
            "storage": {
                "value": body_value,
                "representation": representation,
            }
        },
    }

    result = safe_call(
        confluence.post,
        "rest/api/content",
        data=payload,
    )
    comment_id = result.get("id")
    if not comment_id:
        raise ToolError(f"Unexpected Confluence response: {result}")

    history = result.get("history") or {}
    created_by = history.get("createdBy") or {}
    return {
        "id": comment_id,
        "page_id": page_id,
        "author": created_by.get("displayName") or created_by.get("username"),
        "created": history.get("createdDate"),
        "url": f"{_base()}/pages/viewpage.action?pageId={page_id}#comment-{comment_id}",
    }


# --------- attachments ---------

def confluence_list_attachments(
    page_id: str,
    limit: int = 50,
    start: int = 0,
) -> list[dict]:
    """List attachments on a Confluence page.

    Args:
        page_id: page id.
        limit: max attachments (default 50, up to 200).
        start: pagination offset.

    Returns list of {id, filename, size_bytes, mime, version, download_url, author, created}.
    """
    raw = safe_call(
        confluence.get,
        f"rest/api/content/{page_id}/child/attachment",
        params={
            "expand": "version,metadata,history.createdBy,extensions",
            "limit": min(limit, 200),
            "start": start,
        },
    )
    results = raw.get("results", []) if isinstance(raw, dict) else []
    base = _base()
    out = []
    for a in results:
        metadata = a.get("metadata") or {}
        history = a.get("history") or {}
        created_by = history.get("createdBy") or {}
        download_link = ((a.get("_links") or {}).get("download") or "")
        out.append({
            "id": a.get("id"),
            "filename": a.get("title"),
            "size_bytes": (a.get("extensions") or {}).get("fileSize"),
            "mime": metadata.get("mediaType"),
            "version": (a.get("version") or {}).get("number"),
            "download_url": f"{base}{download_link}" if download_link else None,
            "author": created_by.get("displayName") or created_by.get("username"),
            "created": history.get("createdDate"),
        })
    # Some Confluence builds expose size under extensions.fileSize
    # Re-fetch to grab size if missing (cheap — only on a handful of items).
    return out


def confluence_get_attachment(attachment_id: str) -> dict:
    """Download a Confluence attachment as base64.

    Size limited by MAX_ATTACHMENT_SIZE env (default 2 MB). Larger attachments
    fail with a clear error — use the download_url from confluence_list_attachments
    to fetch them directly via HTTP.

    Args:
        attachment_id: attachment content id.

    Returns {id, filename, mime, size_bytes, data_base64}.
    """
    import requests

    meta = safe_call(
        confluence.get,
        f"rest/api/content/{attachment_id}",
        params={"expand": "version,metadata,container"},
    )
    if meta.get("type") != "attachment":
        raise ToolError(
            f"Content {attachment_id} is not an attachment (type={meta.get('type')})"
        )

    download_link = ((meta.get("_links") or {}).get("download") or "")
    if not download_link:
        raise ToolError(f"No download link found for attachment {attachment_id}")

    url = f"{_base()}{download_link}"

    # Stream to check size, then read into memory if under limit.
    from atlassian_mcp.config import settings as _settings

    headers = {"Authorization": f"Bearer {_settings.confluence_pat}"}
    with requests.get(url, headers=headers, verify=_settings.verify, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        content_length = int(resp.headers.get("Content-Length") or 0)
        if content_length and content_length > _settings.max_attachment_size:
            raise ToolError(
                f"Attachment too large ({content_length} bytes > "
                f"{_settings.max_attachment_size}). "
                f"Use the download_url directly: {url}"
            )
        data = resp.content
        if len(data) > _settings.max_attachment_size:
            raise ToolError(
                f"Attachment too large ({len(data)} bytes > "
                f"{_settings.max_attachment_size}). "
                f"Use the download_url directly: {url}"
            )

    metadata = meta.get("metadata") or {}
    return {
        "id": attachment_id,
        "filename": meta.get("title"),
        "mime": metadata.get("mediaType"),
        "size_bytes": len(data),
        "data_base64": b64encode_bytes(data),
    }


def confluence_upload_attachment(
    page_id: str,
    filename: str,
    data_base64: str,
    mime: str | None = None,
    comment: str | None = None,
) -> dict:
    """Upload (or update) an attachment on a Confluence page.

    Size limited by MAX_ATTACHMENT_SIZE env (default 2 MB).

    Args:
        page_id: target page id.
        filename: attachment filename (used as title and key for versioning).
        data_base64: file content, base64-encoded.
        mime: optional MIME type (e.g. 'image/png', 'application/pdf'). If omitted,
              Confluence will guess from filename.
        comment: optional comment for this attachment version.

    Returns {id, filename, version, download_url}.
    """
    from atlassian_mcp.config import settings as _settings

    raw = b64decode_to_bytes(data_base64)
    if len(raw) > _settings.max_attachment_size:
        raise ToolError(
            f"Attachment too large ({len(raw)} bytes > "
            f"{_settings.max_attachment_size}). Use Confluence UI for large files."
        )

    # atlassian-python-api: attach_content(content, name, content_type, page_id, comment)
    result = safe_call(
        confluence.attach_content,
        content=raw,
        name=filename,
        content_type=mime,
        page_id=page_id,
        comment=comment,
    )

    # attach_content returns a dict with 'results' (on create) or a single attachment dict.
    if isinstance(result, dict) and "results" in result:
        attachments = result.get("results") or []
        if not attachments:
            raise ToolError(f"Upload failed: empty response {result}")
        att = attachments[0]
    else:
        att = result

    att_id = att.get("id")
    download_link = ((att.get("_links") or {}).get("download") or "")
    return {
        "id": att_id,
        "filename": att.get("title") or filename,
        "version": (att.get("version") or {}).get("number"),
        "download_url": f"{_base()}{download_link}" if download_link else None,
    }


# --------- labels ---------

def confluence_add_label(page_id: str, label: str) -> dict:
    """Add a label to a Confluence page.

    Args:
        page_id: page id.
        label: label name (no spaces; use hyphens or underscores).

    Returns {page_id, label, all_labels}.
    """
    # Confluence wants POST with a list of {"prefix": "global", "name": "..."}
    payload = [{"prefix": "global", "name": label}]
    safe_call(
        confluence.post,
        f"rest/api/content/{page_id}/label",
        data=payload,
    )
    # Read back full set of labels.
    raw = safe_call(
        confluence.get,
        f"rest/api/content/{page_id}/label",
    )
    labels = raw.get("results", []) if isinstance(raw, dict) else []
    return {
        "page_id": page_id,
        "label": label,
        "all_labels": [lbl.get("name") for lbl in labels],
    }



# --------- users ---------

def confluence_get_current_user() -> dict:
    """Return the currently authenticated Confluence user (i.e. the owner of CONFLUENCE_PAT).

    Essential for tasks like adding yourself to a signature list (<ri:user ri:userkey="..."/>)
    or knowing your own userKey to filter watchers/mentions.

    Returns {userKey, username, displayName, email, type, profilePicture}.
    """
    data = safe_call(
        confluence.get,
        "rest/api/user/current",
    )
    return {
        "userKey": data.get("userKey"),
        "username": data.get("username"),
        "displayName": data.get("displayName"),
        "email": data.get("email"),
        "type": data.get("type"),
        "profilePicture": (data.get("profilePicture") or {}).get("path"),
    }


def confluence_get_user(identifier: str, by: str = "username") -> dict:
    """Get a Confluence user profile by username or userKey.

    Args:
        identifier: the username (e.g. 'oleg.pokrovskiy') or userKey (hex id).
        by: 'username' or 'key'. Default 'username'.

    Returns {userKey, username, displayName, email, type, profilePicture}.
    """
    if by not in ("username", "key"):
        raise ToolError("by must be 'username' or 'key'")

    param_name = "username" if by == "username" else "key"
    data = safe_call(
        confluence.get,
        "rest/api/user",
        params={param_name: identifier},
    )
    return {
        "userKey": data.get("userKey"),
        "username": data.get("username"),
        "displayName": data.get("displayName"),
        "email": data.get("email"),
        "type": data.get("type"),
        "profilePicture": (data.get("profilePicture") or {}).get("path"),
    }


def confluence_search_users(query: str, limit: int = 25) -> list[dict]:
    """Search Confluence users by displayName or username fragment via CQL.

    Args:
        query: search string (fragment of displayName or username).
        limit: max results (default 25, up to 50).

    Returns list of {userKey, username, displayName, email}.
    """
    # Confluence DC: CQL "user.fullname" matches displayName; wrap query with wildcards.
    cql = f'type = "user" AND user.fullname ~ "{query}"'
    raw = safe_call(
        confluence.get,
        "rest/api/search",
        params={
            "cql": cql,
            "limit": min(limit, 50),
        },
    )
    results = raw.get("results", []) if isinstance(raw, dict) else []
    out = []
    for r in results:
        u = r.get("user") or {}
        out.append({
            "userKey": u.get("userKey"),
            "username": u.get("username"),
            "displayName": u.get("displayName"),
            "email": u.get("email"),
        })
    return out


TOOLS = [
    confluence_list_spaces,
    confluence_get_page,
    confluence_search_by_title,
    confluence_search_cql,
    confluence_get_page_children,
    confluence_get_page_history,
    confluence_create_page,
    confluence_update_page,
    confluence_move_page,
    confluence_get_page_comments,
    confluence_add_comment,
    confluence_list_attachments,
    confluence_get_attachment,
    confluence_upload_attachment,
    confluence_add_label,
    confluence_get_current_user,
    confluence_get_user,
    confluence_search_users,
]
