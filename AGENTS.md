# Agent notes (Cursor)

When this repository is the workspace root, Cursor loads **`.cursor/rules/atlassian-mcp-confluence-edits.mdc`** (`alwaysApply: true`) and the MCP server sends expanded **`instructions`** from `atlassian_mcp/mcp_server.py`.

**Human-readable reference:** `INSTRUCTIONS.md` (Russian) — parameters, error codes, and JSON examples for **`confluence_replace_in_page_storage`** and **`confluence_update_page`** extensions.

If the MCP server is configured in a **different** project root, copy `.cursor/rules/atlassian-mcp-confluence-edits.mdc` into that project’s `.cursor/rules/` or mirror its guidance in your own rules.
