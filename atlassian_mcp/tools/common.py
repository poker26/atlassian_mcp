"""Shared helpers for tool implementations."""
from __future__ import annotations

import base64
import html
import logging
from typing import Any, Callable

import markdown as md

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Raised when a tool call fails in a user-visible way."""


def safe_call(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Call a client method and normalize errors into ToolError.

    For HTTP errors, pull the server-side error message out of the response
    body if the bare exception string is uninformative — Atlassian REST
    typically returns useful context in {"errorMessages": [...], "errors": {}}
    or as plain text, not in the HTTPError repr itself.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log.exception("Tool call failed: %s(%r, %r)", fn.__name__, args, kwargs)
        msg = str(e).strip()
        # Try to enrich from response body for HTTPError-shaped exceptions
        resp = getattr(e, "response", None)
        if resp is not None:
            body_extra = ""
            try:
                # Atlassian standard error envelope
                j = resp.json()
                if isinstance(j, dict):
                    em = j.get("errorMessages") or []
                    errs = j.get("errors") or {}
                    parts = []
                    if em:
                        parts.append("; ".join(str(m) for m in em))
                    if errs:
                        parts.append(
                            ", ".join(f"{k}={v}" for k, v in errs.items())
                        )
                    if parts:
                        body_extra = " | " + " | ".join(parts)
            except Exception:
                # Fall back to first 300 chars of body text
                txt = (getattr(resp, "text", "") or "").strip()
                if txt:
                    body_extra = " | " + txt[:300]
            status = getattr(resp, "status_code", None)
            if status:
                body_extra = f" (HTTP {status}){body_extra}"
            msg = f"{msg}{body_extra}" if msg else f"HTTP {status}{body_extra}"
        raise ToolError(f"{type(e).__name__}: {msg}") from e


def b64encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode_to_bytes(data: str) -> bytes:
    return base64.b64decode(data)


# --------- Unicode surrogate sanitization ---------

def sanitize_str(value: Any) -> Any:
    """Strip lone surrogates (U+D800..DFFF) from strings, keeping valid pairs.

    Atlassian sometimes truncates excerpt/title strings in the middle of a
    surrogate pair (an emoji or flag char encoded as two UTF-16 code units),
    leaving a dangling half-surrogate. That half-surrogate round-trips through
    json.loads into a Python str which UTF-8 cannot encode on the way out.

    The roundtrip trick: encode as UTF-8 with 'surrogatepass' (writes raw
    surrogates as 3-byte sequences), then decode back with 'replace' — lone
    surrogates become U+FFFD, valid surrogate pairs remain intact because at
    decode time they're already valid 4-byte UTF-8 characters.

    Non-string values pass through unchanged. None stays None.
    """
    if not isinstance(value, str):
        return value
    try:
        # Fast path — if the string is already clean, encoding succeeds and we
        # keep the original object (no allocation).
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        return value.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def sanitize_strings(obj: Any) -> Any:
    """Recursively sanitize all strings inside a dict/list structure.

    Cheap: only walks the structure, only allocates when a string actually
    needs fixing.
    """
    if isinstance(obj, str):
        return sanitize_str(obj)
    if isinstance(obj, dict):
        return {k: sanitize_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_strings(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_strings(v) for v in obj)
    return obj


# --------- Pagination envelope helpers ---------

def envelope_full(items: list, *, start_at: int = 0) -> dict:
    """Wrap a fully-fetched list in the standard pagination envelope.

    Use when the underlying API returns the entire list in one response with
    no native pagination (most /rest/api/2/* simple GETs).
    """
    items = items or []
    size = len(items)
    return {
        "results": items,
        "pagination": {
            "start_at": start_at,
            "size": size,
            "total": size,
            "is_last": True,
            "next_start_at": None,
        },
    }


def envelope_paginated(
    items: list,
    *,
    start_at: int,
    limit: int,
    total: int | None = None,
    is_last: bool | None = None,
) -> dict:
    """Wrap a server-paginated page of results.

    Use when the API supports start/limit and returns enough info to know if
    more pages exist. If total is None, has_more is inferred from the
    returned-vs-limit ratio (Confluence-style).
    """
    items = items or []
    size = len(items)
    if is_last is None:
        is_last = size < limit
    next_start = None if is_last else start_at + size
    return {
        "results": items,
        "pagination": {
            "start_at": start_at,
            "limit": limit,
            "size": size,
            "total": total,
            "is_last": is_last,
            "next_start_at": next_start,
        },
    }


# --------- Confluence content format conversion ---------

VALID_FORMATS = ("storage", "wiki", "plain", "markdown")


def to_storage(content: str, content_format: str = "storage") -> tuple[str, str]:
    """Convert content to Confluence representation.

    Returns (body_value, representation) ready to be passed to Confluence API.
    """
    fmt = (content_format or "storage").lower()
    if fmt not in VALID_FORMATS:
        raise ToolError(
            f"Invalid content_format '{content_format}'. "
            f"Must be one of: {', '.join(VALID_FORMATS)}"
        )

    if fmt == "storage":
        return content, "storage"

    if fmt == "wiki":
        return content, "wiki"

    if fmt == "plain":
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        if not paragraphs:
            return "<p></p>", "storage"
        body = "\n".join(
            f"<p>{html.escape(p).replace(chr(10), '<br/>')}</p>"
            for p in paragraphs
        )
        return body, "storage"

    # markdown
    html_body = md.markdown(
        content,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
    )
    return html_body, "storage"
