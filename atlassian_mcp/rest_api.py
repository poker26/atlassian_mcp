"""REST mirror (read-only) for common tool operations.

Write operations (create/update/upload/comment) are intentionally NOT
exposed here — use MCP for those to discourage unguarded scripting.
"""
from fastapi import APIRouter, Depends

from atlassian_mcp.auth import require_api_key

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

# Роуты будут добавлены отдельными файлами rest_jira.py / rest_confluence.py
# или inline, когда появятся тулы. Пока — пусто.
