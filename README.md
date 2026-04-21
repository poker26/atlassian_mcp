# atlassian_mcp

MCP server for Atlassian Data Center (Jira + Confluence), built on FastMCP 2.x and `atlassian-python-api`.

Packaging mirrors [gitlab_mcp](https://github.com/poker26/gitlab_mcp): single ASGI app exposing:

- `POST /mcp/` — MCP streamable HTTP transport (Claude Code, Claude Desktop, n8n MCP Client)
- `GET /api/v1/*` — read-only REST mirror of common operations (planned)
- `GET /health` — unauthenticated liveness + Jira/Confluence reachability probe
- `GET /docs` — Swagger UI for REST side

## Tools (21)

### Jira (6)
| Tool | Purpose |
|------|---------|
| `jira_search` | JQL search, returns key/summary/status/assignee/priority |
| `jira_get_issue` | Full issue details incl. comments, attachments, labels |
| `jira_create_issue` | Create new issue with project, summary, type, labels, etc. |
| `jira_update_issue` | Update summary/description/priority/assignee/labels |
| `jira_add_comment` | Add a comment to an issue |
| `jira_transition_issue` | Change issue status; pass unknown transition to list available |

### Confluence (15)
| Tool | Purpose |
|------|---------|
| `confluence_list_spaces` | List accessible spaces (paginated) |
| `confluence_get_page` | Page by id with body in storage format |
| `confluence_search_by_title` | Exact-title lookup within a space |
| `confluence_search_cql` | Full-text CQL search |
| `confluence_get_page_children` | Direct child pages |
| `confluence_get_page_history` | Version history |
| `confluence_create_page` | Create page (storage/wiki/plain/markdown input) |
| `confluence_update_page` | Update page content/title with auto version bump |
| `confluence_move_page` | ⚠️ Not supported on 7.19 — returns a clear error |
| `confluence_get_page_comments` | List footer/inline comments |
| `confluence_add_comment` | Add a footer comment |
| `confluence_list_attachments` | List attachments on a page |
| `confluence_get_attachment` | Download attachment as base64 (2 MB limit) |
| `confluence_upload_attachment` | Upload attachment from base64 (2 MB limit) |
| `confluence_add_label` | Add a label to a page |

Write tools for Confluence accept `content_format` = `"storage"` (XHTML subset), `"wiki"` (legacy markup), `"plain"` (auto-wrapped in `<p>`), or `"markdown"` (converted via Python `markdown` with fenced_code, tables, nl2br).

## Authentication

- `/mcp/*` — `X-API-Key` header (or `Authorization: Bearer <key>`), middleware-enforced.
- `/api/v1/*` — `X-API-Key` via FastAPI dependency.
- `/health`, `/docs`, `/openapi.json` — public.

Rotate the key by editing `.env` and restarting (`docker compose up -d --force-recreate`).

## Quick start

```bash
git clone https://github.com/poker26/atlassian_mcp.git
cd atlassian_mcp

cp .env.example .env
# fill in JIRA_*, CONFLUENCE_*, MCP_API_KEY (generate: openssl rand -hex 32)

docker compose up -d --build
docker compose logs -f

curl -s http://localhost:8002/health | python3 -m json.tool
```

## Deployment notes

The `docker-compose.yml` uses `network_mode: host` so the container can reach
internal corporate services through VPN interfaces on the host (e.g. OpenConnect
tun0). `/etc/resolv.conf` is mounted read-only so the container picks up
corporate DNS dynamically, including after VPN reconnects.

## Client configuration

### Claude Code / Desktop (`~/.claude/mcp.json`)

```json
{
  "mcpServers": {
    "atlassian": {
      "type": "http",
      "url": "https://atlassianmcp.example.com/mcp/",
      "headers": {
        "X-API-Key": "<MCP_API_KEY from .env>"
      }
    }
  }
}
```

### n8n MCP Client Tool

- URL: `http://<host>:8002/mcp/` (trailing slash matters)
- Transport: HTTP Streamable
- Custom header: `X-API-Key: <key>`

## License

MIT.
