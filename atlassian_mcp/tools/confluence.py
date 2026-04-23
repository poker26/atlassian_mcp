"""Confluence tools (REST API v1 via atlassian-python-api, Confluence 7.19 DC)."""
from __future__ import annotations

from typing import Any

import requests

from atlassian_mcp.clients import confluence
from atlassian_mcp.config import settings
from atlassian_mcp.tools.common import (
    ToolError,
    b64decode_to_bytes,
    b64encode_bytes,
    safe_call,
    to_storage,
)
from atlassian_mcp.tools.url_fetch import fetch_url


def _base() -> str:
    return confluence.url.rstrip("/")


# --------- read ---------

def confluence_list_spaces(limit: int = 25, start: int = 0) -> list[dict]:
    """List Confluence spaces visible to the authenticated user.

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
    """Get a Confluence page by id: title, space, version, body (storage format), URL."""
    expand = "space,version,body.storage" if include_body else "space,version"
    page = safe_call(confluence.get_page_by_id, page_id, expand=expand)
    return {
        "id": page.get("id"),
        "title": page.get("title"),
        "space_key": (page.get("space") or {}).get("key"),
        "space_name": (page.get("space") or {}).get("name"),
        "version": (page.get("version") or {}).get("number"),
        "url": f"{_base()}/pages/viewpage.action?pageId={page.get('id')}",
        "body_storage": ((page.get("body") or {}).get("storage") or {}).get("value")
                        if include_body else None,
    }


def confluence_search_by_title(space_key: str, title: str, limit: int = 10) -> list[dict]:
    """Find a Confluence page in a space by exact title match.

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

    Returns list of {id, type, title, space_key, url, excerpt}.
    """
    raw = safe_call(
        confluence.cql,
        cql,
        limit=min(limit, 50),
        expand="content.space",
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

    Returns list of {id, title, url}.
    """
    raw = safe_call(
        confluence.get_page_child_by_type,
        page_id,
        type="page",
        start=start,
        limit=min(limit, 200),
    )
    results = raw if isinstance(raw, list) else (
        raw.get("results", []) if isinstance(raw, dict) else []
    )
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
        content_format: one of 'storage', 'wiki', 'plain', 'markdown'.

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
    """Update an existing Confluence page. Version auto-increments."""
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


def confluence_copy_page(
    source_page_id: str,
    target_parent_id: str,
    new_title: str | None = None,
    target_space_key: str | None = None,
    include_attachments: bool = True,
    include_labels: bool = True,
) -> dict:
    """Copy a Confluence page under a different parent.

    Creates a new page with the source page's body (storage format) under
    `target_parent_id`. This is a workaround for the missing native move/copy
    REST endpoint on Confluence DC 7.19. Always creates a new page — the
    source page is left untouched.

    What is NOT carried over (and listed in `warnings`):
    - Page history / prior versions
    - Comments (footer and inline)
    - Watchers
    - Page restrictions
    - Inbound links to the source page (they continue to point to source)

    What IS carried over when flags are True:
    - Page body (storage format)
    - Attachments (re-uploaded; file size limited by MAX_URL_FETCH_SIZE)
    - Labels

    Args:
        source_page_id: page to copy from.
        target_parent_id: parent under which the new page is created.
        new_title: title of the new page. Defaults to source title.
                   Must be unique within the target space.
        target_space_key: space key. Defaults to the source page's space.
        include_attachments: re-upload attachments to the new page.
        include_labels: copy labels to the new page.

    Returns {id, title, url, warnings, copied: {attachments, labels}}.
    """
    src = safe_call(
        confluence.get_page_by_id,
        source_page_id,
        expand="space,version,body.storage",
    )
    body = ((src.get("body") or {}).get("storage") or {}).get("value", "")
    src_space = (src.get("space") or {}).get("key")
    space_key = target_space_key or src_space
    if not space_key:
        raise ToolError(
            f"Cannot determine target space — source page {source_page_id} "
            "has no space and target_space_key was not provided."
        )

    title = new_title or src.get("title")
    if not title:
        raise ToolError(f"Source page {source_page_id} has no title")

    created = safe_call(
        confluence.create_page,
        space=space_key,
        title=title,
        body=body,
        parent_id=target_parent_id,
        representation="storage",
    )
    new_id = created.get("id")
    if not new_id:
        raise ToolError(f"Unexpected Confluence create response: {created}")

    copied_attachments: list[dict] = []
    attachment_errors: list[str] = []
    if include_attachments:
        atts = safe_call(
            confluence.get,
            f"rest/api/content/{source_page_id}/child/attachment",
            params={"limit": 200, "expand": "version,metadata"},
        )
        att_results = atts.get("results", []) if isinstance(atts, dict) else []
        for a in att_results:
            aid = a.get("id")
            filename = a.get("title")
            mime = (a.get("metadata") or {}).get("mediaType")
            dl = ((a.get("_links") or {}).get("download") or "")
            if not aid or not filename or not dl:
                continue
            try:
                # Stream attachment to memory (size-bound by MAX_URL_FETCH_SIZE)
                headers = {"Authorization": f"Bearer {settings.confluence_pat}"}
                with requests.get(
                    f"{_base()}{dl}",
                    headers=headers,
                    verify=settings.verify,
                    stream=True,
                    timeout=60,
                ) as resp:
                    resp.raise_for_status()
                    data = bytearray()
                    for chunk in resp.iter_content(64 * 1024):
                        data.extend(chunk)
                        if len(data) > settings.max_url_fetch_size:
                            raise ToolError(
                                f"Attachment {filename} exceeds "
                                f"{settings.max_url_fetch_size} bytes"
                            )
                safe_call(
                    confluence.attach_content,
                    content=bytes(data),
                    name=filename,
                    content_type=mime,
                    page_id=new_id,
                )
                copied_attachments.append({
                    "filename": filename,
                    "size_bytes": len(data),
                })
            except Exception as e:
                attachment_errors.append(f"{filename}: {type(e).__name__}: {e}")

    copied_labels: list[str] = []
    label_errors: list[str] = []
    if include_labels:
        try:
            raw = safe_call(
                confluence.get,
                f"rest/api/content/{source_page_id}/label",
            )
            labels = raw.get("results", []) if isinstance(raw, dict) else []
            payload = [
                {"prefix": lbl.get("prefix") or "global", "name": lbl.get("name")}
                for lbl in labels if lbl.get("name")
            ]
            if payload:
                safe_call(
                    confluence.post,
                    f"rest/api/content/{new_id}/label",
                    data=payload,
                )
                copied_labels = [lbl["name"] for lbl in payload]
        except Exception as e:
            label_errors.append(f"{type(e).__name__}: {e}")

    warnings = [
        "history not copied — new page starts at version 1",
        "comments not copied (footer + inline)",
        "watchers not copied",
        "page restrictions not copied",
        "inbound links still point to source page",
    ]
    if attachment_errors:
        warnings.append(f"attachment errors: {attachment_errors}")
    if label_errors:
        warnings.append(f"label errors: {label_errors}")

    return {
        "id": new_id,
        "title": created.get("title"),
        "url": f"{_base()}/pages/viewpage.action?pageId={new_id}",
        "source_page_id": source_page_id,
        "copied": {
            "attachments": copied_attachments,
            "labels": copied_labels,
        },
        "warnings": warnings,
    }


# --------- comments ---------

def confluence_get_page_comments(
    page_id: str,
    limit: int = 25,
    start: int = 0,
    location: str = "footer",
) -> list[dict]:
    """Get comments attached to a Confluence page.

    Args:
        location: 'footer' (default), 'inline', or 'all'.
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
    """Add a footer comment to a Confluence page."""
    body_value, representation = to_storage(comment, content_format)
    payload = {
        "type": "comment",
        "container": {"id": page_id, "type": "page"},
        "body": {
            "storage": {"value": body_value, "representation": representation}
        },
    }
    result = safe_call(confluence.post, "rest/api/content", data=payload)
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
    return out


def confluence_get_attachment(attachment_id: str) -> dict:
    """Download a Confluence attachment as base64.

    Size limited by MAX_ATTACHMENT_SIZE (default 2 MB).

    Returns {id, filename, mime, size_bytes, data_base64}.
    """
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

    headers = {"Authorization": f"Bearer {settings.confluence_pat}"}
    with requests.get(
        url, headers=headers, verify=settings.verify,
        stream=True, timeout=30,
    ) as resp:
        resp.raise_for_status()
        content_length = int(resp.headers.get("Content-Length") or 0)
        if content_length and content_length > settings.max_attachment_size:
            raise ToolError(
                f"Attachment too large ({content_length} bytes > "
                f"{settings.max_attachment_size}). Use the download_url: {url}"
            )
        data = resp.content
        if len(data) > settings.max_attachment_size:
            raise ToolError(
                f"Attachment too large ({len(data)} bytes > "
                f"{settings.max_attachment_size}). Use the download_url: {url}"
            )

    metadata = meta.get("metadata") or {}
    return {
        "id": attachment_id,
        "filename": meta.get("title"),
        "mime": metadata.get("mediaType"),
        "size_bytes": len(data),
        "data_base64": b64encode_bytes(data),
    }


def _confluence_upload_raw(
    page_id: str,
    filename: str,
    data: bytes,
    mime: str | None,
    comment: str | None,
) -> dict:
    """Single-attachment Confluence upload; normalized shape."""
    result = safe_call(
        confluence.attach_content,
        content=data,
        name=filename,
        content_type=mime,
        page_id=page_id,
        comment=comment,
    )
    if isinstance(result, dict) and "results" in result:
        results = result.get("results") or []
        if not results:
            raise ToolError(f"Upload failed: empty response {result}")
        att = results[0]
    else:
        att = result
    download_link = ((att.get("_links") or {}).get("download") or "")
    return {
        "id": att.get("id"),
        "filename": att.get("title") or filename,
        "version": (att.get("version") or {}).get("number"),
        "download_url": f"{_base()}{download_link}" if download_link else None,
    }


def confluence_upload_attachment(
    page_id: str,
    filename: str,
    data_base64: str,
    mime: str | None = None,
    comment: str | None = None,
) -> dict:
    """Upload (or update) an attachment on a Confluence page from base64.

    Size limited by MAX_ATTACHMENT_SIZE (default 2 MB). For larger files use
    confluence_attach_from_url with a public download URL.

    Returns {id, filename, version, download_url}.
    """
    raw = b64decode_to_bytes(data_base64)
    if len(raw) > settings.max_attachment_size:
        raise ToolError(
            f"Attachment too large ({len(raw)} bytes > "
            f"{settings.max_attachment_size}). "
            f"Use confluence_attach_from_url instead."
        )
    return _confluence_upload_raw(page_id, filename, raw, mime, comment)


def confluence_attach_from_url(
    page_id: str,
    url: str,
    filename: str | None = None,
    mime: str | None = None,
    comment: str | None = None,
) -> dict:
    """Download a file from a public URL and attach it to a Confluence page.

    URL validation: only http/https; private/reserved IPs rejected; redirects
    capped at 5. Size capped by MAX_URL_FETCH_SIZE (default 10 MB).

    Args:
        page_id: target page id.
        url: public http(s) URL.
        filename: override auto-detection.
        mime: override Content-Type from response.
        comment: optional attachment-version comment.

    Returns {id, filename, version, download_url, source_url}.
    """
    fetched = fetch_url(url, filename=filename, mime=mime)
    result = _confluence_upload_raw(
        page_id, fetched.filename, fetched.data, fetched.mime, comment,
    )
    result["source_url"] = url
    return result


# --------- labels ---------

def confluence_add_label(page_id: str, label: str) -> dict:
    """Add a label to a Confluence page.

    Returns {page_id, label, all_labels}.
    """
    payload = [{"prefix": "global", "name": label}]
    safe_call(
        confluence.post,
        f"rest/api/content/{page_id}/label",
        data=payload,
    )
    raw = safe_call(confluence.get, f"rest/api/content/{page_id}/label")
    labels = raw.get("results", []) if isinstance(raw, dict) else []
    return {
        "page_id": page_id,
        "label": label,
        "all_labels": [lbl.get("name") for lbl in labels],
    }


# --------- users ---------

def confluence_get_current_user() -> dict:
    """Return the currently authenticated Confluence user."""
    data = safe_call(confluence.get, "rest/api/user/current")
    return {
        "userKey": data.get("userKey"),
        "username": data.get("username"),
        "displayName": data.get("displayName"),
        "email": data.get("email"),
        "type": data.get("type"),
        "profilePicture": (data.get("profilePicture") or {}).get("path"),
    }


def confluence_get_user(identifier: str, by: str = "username") -> dict:
    """Get a Confluence user profile by username or userKey."""
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
    """Search Confluence users by displayName via CQL.

    Args:
        query: search string. Special CQL characters (quotes, backslashes)
               are escaped.
        limit: max results (default 25, up to 50).
    """
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    cql = f'type = "user" AND user.fullname ~ "{escaped}"'
    raw = safe_call(
        confluence.get,
        "rest/api/search",
        params={"cql": cql, "limit": min(limit, 50)},
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
    confluence_copy_page,
    confluence_get_page_comments,
    confluence_add_comment,
    confluence_list_attachments,
    confluence_get_attachment,
    confluence_upload_attachment,
    confluence_attach_from_url,
    confluence_add_label,
    confluence_get_current_user,
    confluence_get_user,
    confluence_search_users,
]
