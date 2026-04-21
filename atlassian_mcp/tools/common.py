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
    """Call a client method and normalize errors into ToolError."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log.exception("Tool call failed: %s(%r, %r)", fn.__name__, args, kwargs)
        raise ToolError(f"{type(e).__name__}: {e}") from e


def b64encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode_to_bytes(data: str) -> bytes:
    return base64.b64decode(data)


# --------- Confluence content format conversion ---------

VALID_FORMATS = ("storage", "wiki", "plain", "markdown")


def to_storage(content: str, content_format: str = "storage") -> tuple[str, str]:
    """Convert content to Confluence representation.

    Returns (body_value, representation) ready to be passed to Confluence API.

    - storage: returned as-is, representation='storage'.
    - wiki: returned as-is, representation='wiki' (Confluence server-side converts).
    - plain: escaped and wrapped in <p>, representation='storage'.
    - markdown: rendered to HTML, representation='storage'.
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
        # Split on blank lines, escape, wrap each paragraph.
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        if not paragraphs:
            return "<p></p>", "storage"
        body = "\n".join(f"<p>{html.escape(p).replace(chr(10), '<br/>')}</p>" for p in paragraphs)
        return body, "storage"

    # markdown
    # extensions: fenced_code (```lang blocks), tables, sane_lists.
    html_body = md.markdown(
        content,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
    )
    return html_body, "storage"
