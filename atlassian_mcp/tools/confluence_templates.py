"""Confluence page templates and page-from-template instantiation.

Wraps /rest/experimental/template on Confluence Data Center 7.19.

Note on lifecycle: Confluence DC 7.19 exposes only CREATE and READ for
templates over REST. There is no working endpoint for UPDATE (PUT /template/{id}
returns 405; POST /template with an existing templateId silently creates a
duplicate) and no endpoint for DELETE. Edits and deletions must be done in
the Confluence UI (Space Tools -> Content Tools -> Templates). Choose names
carefully and use `if_exists='skip'` to make creation idempotent across runs.
"""
from __future__ import annotations

import re
from typing import Any

from atlassian_mcp.clients import confluence
from atlassian_mcp.tools.common import (
    ToolError,
    envelope_paginated,
    safe_call,
    sanitize_str,
    sanitize_strings,
)


def _base() -> str:
    return confluence.url.rstrip("/")


def _shape_template(t: dict) -> dict:
    """Normalize a template response into the MCP shape."""
    space = t.get("space") or {}
    body_storage = ((t.get("body") or {}).get("storage") or {}).get("value")
    return {
        "id": t.get("templateId"),
        "name": t.get("name"),
        "description": t.get("description") or "",
        "template_type": t.get("templateType"),
        "space_key": space.get("key"),
        "space_name": space.get("name"),
        "labels": [
            l.get("name") for l in (t.get("labels") or [])
            if l.get("name")
        ],
        "body_storage": body_storage,
    }


# --------- variable parsing ---------

# <at:var at:name="X"/> or <at:var at:name="X" /> or <at:var at:name="X"></at:var>
# We accept single or double quotes around the name, just in case.
_VAR_PATTERN = re.compile(
    r'<at:var\s+at:name=(["\'])(?P<name>[^"\']+)\1\s*/>'
    r'|'
    r'<at:var\s+at:name=(["\'])(?P<name2>[^"\']+)\3\s*></at:var>'
)

# <at:declarations>...<at:string at:name="X"/>...</at:declarations>
_DECL_BLOCK_PATTERN = re.compile(
    r'<at:declarations>(?P<inner>.*?)</at:declarations>',
    re.DOTALL,
)
_DECL_FIELD_PATTERN = re.compile(
    r'<at:[a-zA-Z]+\s+at:name=(["\'])(?P<name>[^"\']+)\1\s*/?>',
)


def _extract_vars(body_storage: str) -> tuple[set[str], set[str]]:
    """Return (declared_vars, used_vars).

    declared_vars: from <at:declarations> block at the top of the template.
    used_vars: from <at:var at:name="..."> occurrences in the body.

    Either set may be empty; well-formed templates declare and use the
    same names, but we don't enforce it — Confluence itself doesn't.
    """
    declared: set[str] = set()
    for block in _DECL_BLOCK_PATTERN.finditer(body_storage or ""):
        for m in _DECL_FIELD_PATTERN.finditer(block.group("inner")):
            declared.add(m.group("name"))

    used: set[str] = set()
    for m in _VAR_PATTERN.finditer(body_storage or ""):
        used.add(m.group("name") or m.group("name2"))

    return declared, used


def _substitute_vars(body_storage: str, variables: dict) -> str:
    """Replace <at:var at:name="X"/> with str(variables["X"]).

    Variables not present in the dict are left as-is — caller sees them in
    the resulting page body and via the unresolved warnings. Numbers and
    booleans are coerced to str. None is treated as empty string.

    Also strips the <at:declarations>...</at:declarations> block: it's only
    meaningful in templates, not in regular pages.
    """
    out = body_storage or ""

    # Drop declarations block(s) — not valid storage format on regular pages
    out = _DECL_BLOCK_PATTERN.sub("", out)

    def repl(m: re.Match) -> str:
        name = m.group("name") or m.group("name2")
        if name not in variables:
            return m.group(0)  # leave the at:var tag in place
        val = variables[name]
        if val is None:
            return ""
        # XML-escape the substitution to avoid breaking storage format if
        # someone passes "<b>bold</b>" — Confluence storage is XHTML and
        # raw angle brackets in text nodes are illegal.
        return (
            str(val)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    return _VAR_PATTERN.sub(repl, out)


# --------- read ---------

def confluence_get_template(template_id: str) -> dict:
    """Get a Confluence template by id.

    Returns {id, name, description, template_type, space_key, space_name,
             labels, body_storage}.
    """
    data = safe_call(
        confluence.get,
        f"rest/experimental/template/{template_id}",
        params={"expand": "body,space,labels"},
    )
    return sanitize_strings(_shape_template(data))


def confluence_list_templates(
    space_key: str,
    limit: int = 25,
    start_at: int = 0,
) -> dict:
    """List page templates defined in a Confluence space.

    Args:
        space_key: target space (e.g. 'PP').
        limit: page size (default 25, up to 50).
        start_at: pagination offset.

    Returns {results, pagination} where each result is a slim
    {id, name, description, template_type, labels} (no body to keep payloads small).
    """
    page_limit = min(limit, 50)
    raw = safe_call(
        confluence.get,
        "rest/experimental/template/page",
        params={
            "spaceKey": space_key,
            "limit": page_limit,
            "start": start_at,
            "expand": "labels",
        },
    )
    if not isinstance(raw, dict):
        raise ToolError(f"Unexpected list-templates response: {raw}")

    results = raw.get("results") or []
    items = []
    for t in results:
        items.append({
            "id": t.get("templateId"),
            "name": sanitize_str(t.get("name")),
            "description": sanitize_str(t.get("description") or ""),
            "template_type": t.get("templateType"),
            "labels": [
                l.get("name") for l in (t.get("labels") or [])
                if l.get("name")
            ],
        })
    return envelope_paginated(items, start_at=start_at, limit=page_limit)


# --------- write ---------

def _find_template_by_name(space_key: str, name: str) -> dict | None:
    """Look up a template by exact name in a space. Walks all pages."""
    start = 0
    page_limit = 50
    while True:
        raw = safe_call(
            confluence.get,
            "rest/experimental/template/page",
            params={
                "spaceKey": space_key,
                "limit": page_limit,
                "start": start,
                "expand": "body,space,labels",
            },
        )
        if not isinstance(raw, dict):
            return None
        results = raw.get("results") or []
        for t in results:
            if (t.get("name") or "") == name:
                return t
        if len(results) < page_limit:
            return None
        start += page_limit


def confluence_create_template(
    space_key: str,
    name: str,
    body: str,
    description: str | None = None,
    labels: list[str] | None = None,
    if_exists: str = "error",
) -> dict:
    """Create a Confluence page template.

    Args:
        space_key: target space.
        name: template name (must be unique in the space).
        body: template body in storage format. Use <at:var at:name="X"/>
              for variable placeholders. Optionally start with an
              <at:declarations> block listing expected variables —
              confluence_create_page_from_template will report missing ones.
        description: optional human description shown in the template browser.
        labels: optional list of labels.
        if_exists: collision strategy when a template with the same name
                   already exists in this space:
                   - 'error' (default): raise ToolError.
                   - 'skip':  return the existing template unchanged.
                   'update' is intentionally NOT supported: Confluence DC 7.19
                   has no working REST endpoint to update an existing template.
                   PUT on /template/{id} is rejected (405); POST on /template
                   with templateId in the payload silently creates a duplicate
                   under the same name. To edit an existing template, use the
                   Confluence UI: Space tools -> Content Tools -> Templates.

    Returns the created or pre-existing template shape.
    """
    if if_exists not in ("error", "skip"):
        raise ToolError(
            "if_exists must be 'error' or 'skip'. "
            "'update' is unsupported on Confluence DC 7.19 — see docstring."
        )

    existing = _find_template_by_name(space_key, name)
    if existing is not None:
        if if_exists == "skip":
            return sanitize_strings(_shape_template(existing))
        # error
        raise ToolError(
            f"Template named '{name}' already exists in space '{space_key}' "
            f"(id={existing.get('templateId')}). "
            f"Use if_exists='skip' to keep it as-is."
        )

    payload: dict[str, Any] = {
        "name": name,
        "templateType": "page",
        "body": {
            "storage": {"value": body, "representation": "storage"}
        },
        "space": {"key": space_key},
    }
    if description is not None:
        payload["description"] = description
    if labels:
        payload["labels"] = [{"name": l, "prefix": "global"} for l in labels]

    result = safe_call(
        confluence.post,
        "rest/experimental/template",
        data=payload,
    )
    if not isinstance(result, dict) or not result.get("templateId"):
        raise ToolError(f"Unexpected create-template response: {result}")
    return sanitize_strings(_shape_template(result))


# --------- instantiation ---------

def confluence_create_page_from_template(
    template_id: str,
    title: str,
    space_key: str | None = None,
    parent_id: str | None = None,
    variables: dict | None = None,
) -> dict:
    """Create a new Confluence page by instantiating a template.

    Workflow:
      1. Fetch the template body (storage format).
      2. Strip the <at:declarations> block.
      3. Substitute every <at:var at:name="X"/> with variables["X"]
         (HTML-escaped). Unknown vars stay in place and surface in warnings.
      4. Create the page under parent_id in space_key.

    Args:
        template_id: template to instantiate.
        title: title of the new page (must be unique in the space).
        space_key: target space. Defaults to the template's space.
        parent_id: optional parent page id. If omitted, the page is created
                   at the space root.
        variables: dict of {placeholder_name: value}. Values are coerced to
                   str (None -> ""), then HTML-escaped before insertion.

    Returns {id, title, version, url, warnings, used_vars, declared_vars,
             missing_vars, unused_vars}.
    """
    variables = variables or {}

    template = safe_call(
        confluence.get,
        f"rest/experimental/template/{template_id}",
        params={"expand": "body,space"},
    )
    template_space = (template.get("space") or {}).get("key")
    target_space = space_key or template_space
    if not target_space:
        raise ToolError(
            f"Cannot determine target space — template {template_id} has no "
            "space and space_key was not provided."
        )

    body_storage = ((template.get("body") or {}).get("storage") or {}).get("value", "")

    declared, used = _extract_vars(body_storage)
    provided = set(variables.keys())

    # warnings collection
    warnings: list[str] = []
    missing = sorted(used - provided)
    unused = sorted(provided - used - declared)
    declared_not_used = sorted(declared - used)

    if missing:
        warnings.append(
            f"variables left unresolved (still <at:var ...> in page body): "
            f"{missing}"
        )
    if unused:
        warnings.append(
            f"variables provided but template doesn't use them: {unused}"
        )
    if declared_not_used:
        warnings.append(
            f"template declared but did not reference: {declared_not_used}"
        )

    rendered_body = _substitute_vars(body_storage, variables)

    create_payload: dict[str, Any] = {
        "type": "page",
        "title": title,
        "space": {"key": target_space},
        "body": {
            "storage": {"value": rendered_body, "representation": "storage"}
        },
    }
    if parent_id:
        create_payload["ancestors"] = [{"id": str(parent_id)}]

    result = safe_call(
        confluence.post,
        "rest/api/content",
        data=create_payload,
    )
    page_id = result.get("id") if isinstance(result, dict) else None
    if not page_id:
        raise ToolError(f"Unexpected create-page response: {result}")

    return sanitize_strings({
        "id": page_id,
        "title": result.get("title"),
        "version": ((result.get("version") or {}).get("number") or 1),
        "url": f"{_base()}/pages/viewpage.action?pageId={page_id}",
        "template_id": template_id,
        "used_vars": sorted(used),
        "declared_vars": sorted(declared),
        "missing_vars": missing,
        "unused_vars": unused,
        "warnings": warnings,
    })


TOOLS = [
    confluence_get_template,
    confluence_list_templates,
    confluence_create_template,
    confluence_create_page_from_template,
]
