"""All tool registrations."""
from atlassian_mcp.tools.confluence import TOOLS as CONFLUENCE_TOOLS
from atlassian_mcp.tools.jira import TOOLS as JIRA_TOOLS

ALL_TOOLS = JIRA_TOOLS + CONFLUENCE_TOOLS
