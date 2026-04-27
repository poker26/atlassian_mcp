"""Application settings loaded from environment."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Jira
    jira_url: str
    jira_user: str
    jira_pat: str

    # Confluence
    confluence_url: str
    confluence_pat: str

    # Shared
    ssl_verify: str | bool = "true"

    # MCP server
    mcp_api_key: str
    server_host: str = "0.0.0.0"
    server_port: int = 8002
    log_level: str = "INFO"

    # Attachments
    max_attachment_size: int = 2 * 1024 * 1024       # 2 MB for base64 via MCP
    max_url_fetch_size: int = 10 * 1024 * 1024       # 10 MB for attach_from_url

    @property
    def verify(self) -> bool | str:
        """Turn SSL_VERIFY into what requests/atlassian-python-api expects."""
        v = str(self.ssl_verify).strip()
        if v.lower() in ("false", "0", "no"):
            return False
        if v.lower() in ("true", "1", "yes"):
            return True
        # Path to CA bundle
        return v


settings = Settings()
