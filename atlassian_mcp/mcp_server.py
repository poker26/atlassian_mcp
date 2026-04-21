"""FastMCP server with tool registration."""
import logging

from fastmcp import FastMCP

from atlassian_mcp import __version__
from atlassian_mcp.tools import ALL_TOOLS

log = logging.getLogger(__name__)

mcp = FastMCP(
    name="atlassian-mcp",
    instructions=(
        "Atlassian MCP server exposing Jira and Confluence Data Center. "
        "Jira tools wrap REST API v2 via atlassian-python-api. "
        "Confluence tools wrap REST API v1 via atlassian-python-api. "
        "Most Confluence write tools accept content_format='storage'|'wiki'|'plain'|'markdown'."
    ),
)

for tool_fn in ALL_TOOLS:
    mcp.tool()(tool_fn)

log.info("MCP server '%s' v%s registered %d tools", mcp.name, __version__, len(ALL_TOOLS))
