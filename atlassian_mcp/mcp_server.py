"""FastMCP server with tool registration."""
import logging

from fastmcp import FastMCP

from atlassian_mcp import __version__
from atlassian_mcp.tools import ALL_TOOLS

log = logging.getLogger(__name__)

mcp = FastMCP(
    name="atlassian-mcp",
    instructions=(
        "Atlassian Data Center MCP: Jira (REST v2) + Confluence (REST v1) via atlassian-python-api. "
        "Confluence write tools accept content_format='storage'|'wiki'|'plain'|'markdown' unless noted.\n\n"
        "EDITING CONFLUENCE PAGE BODY (agents): Prefer confluence_replace_in_page_storage over "
        "confluence_update_page when changing existing storage HTML. The server GETs the page, "
        "applies ordered replacements[{find, replace, match: literal|regex, max_occurrences?}], "
        "validates HTML, then PUTs with the next version. Workflow: (1) confluence_get_page for "
        "page_id and version if you need expected_version; (2) confluence_replace_in_page_storage "
        "with dry_run=true and inspect total_occurrences_applied and per-rule occurrences; "
        "(3) repeat with dry_run=false to write. On VERSION_CONFLICT, refresh version from "
        "confluence_get_page and retry. status no_op means no PUT (idempotent). "
        "Use fail_if_no_match=true only when zero matches must be an error.\n\n"
        "confluence_update_page: full body replace; optional expected_version (optimistic lock, "
        "StructuredToolError VERSION_CONFLICT on mismatch); optional content_encoding=base64 "
        "for large UTF-8 payloads after base64 decode; optional version_comment.\n\n"
        "Repository INSTRUCTIONS.md and .cursor/rules/atlassian-mcp-confluence-edits.mdc expand "
        "this guidance for Cursor."
    ),
)

for tool_fn in ALL_TOOLS:
    mcp.tool()(tool_fn)

log.info("MCP server '%s' v%s registered %d tools", mcp.name, __version__, len(ALL_TOOLS))
