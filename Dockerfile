FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY atlassian_mcp ./atlassian_mcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -sf http://localhost:${SERVER_PORT:-8002}/health | grep -q '"status":"ok"' || exit 1

CMD ["python", "-m", "atlassian_mcp.main"]
