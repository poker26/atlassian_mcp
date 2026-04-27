"""All tool registrations."""
from atlassian_mcp.tools.confluence import TOOLS as CONFLUENCE_TOOLS
from atlassian_mcp.tools.confluence_lifecycle import TOOLS as CONFLUENCE_LIFECYCLE_TOOLS
from atlassian_mcp.tools.confluence_macros import TOOLS as CONFLUENCE_MACROS_TOOLS
from atlassian_mcp.tools.confluence_restrictions import TOOLS as CONFLUENCE_RESTRICTIONS_TOOLS
from atlassian_mcp.tools.confluence_templates import TOOLS as CONFLUENCE_TEMPLATES_TOOLS
from atlassian_mcp.tools.jira import TOOLS as JIRA_TOOLS
from atlassian_mcp.tools.jira_boards import TOOLS as JIRA_BOARDS_TOOLS
from atlassian_mcp.tools.jira_filters import TOOLS as JIRA_FILTERS_TOOLS
from atlassian_mcp.tools.jira_meta import TOOLS as JIRA_META_TOOLS

ALL_TOOLS = (
    JIRA_TOOLS
    + JIRA_META_TOOLS
    + JIRA_FILTERS_TOOLS
    + JIRA_BOARDS_TOOLS
    + CONFLUENCE_TOOLS
    + CONFLUENCE_TEMPLATES_TOOLS
    + CONFLUENCE_RESTRICTIONS_TOOLS
    + CONFLUENCE_LIFECYCLE_TOOLS
    + CONFLUENCE_MACROS_TOOLS
)
