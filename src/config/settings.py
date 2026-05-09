from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Layer 2: Provider selection ──────────────────────────────────────────
    # Choices: "openai" | "azure_openai" | "bedrock"
    llm_provider: str = "openai"

    # ── OpenAI ───────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model_heavy: str = "gpt-4o"        # discovery, planning
    openai_model_light: str = "gpt-4o-mini"   # inference, chat

    # ── Azure OpenAI ─────────────────────────────────────────────────────────
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_deployment_heavy: str = "gpt-4o"
    azure_deployment_light: str = "gpt-4o-mini"

    # ── AWS Bedrock ──────────────────────────────────────────────────────────
    aws_region: str = "us-east-1"
    bedrock_model_heavy: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_model_light: str = "anthropic.claude-3-haiku-20240307-v1:0"

    # ── Layer 7: Database ────────────────────────────────────────────────────
    # Supports any SQLAlchemy URI: sqlite / postgresql / mysql / mssql / snowflake
    database_url: str = "sqlite:///data/chinook.db"

    # ── Layer 1: App ─────────────────────────────────────────────────────────
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: str = "*"       # comma-separated or "*"

    # ── Layer 3: Agent ───────────────────────────────────────────────────────
    agent_max_iterations: int = 10
    max_result_rows: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
