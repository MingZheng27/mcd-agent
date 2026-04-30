from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    llm_provider: str = Field(default="minimax", alias="LLM_PROVIDER")

    minimax_api_key: str = Field(default="", alias="MINIMAX_API_KEY")
    minimax_base_url: str = Field(default="https://api.minimax.io/v1", alias="MINIMAX_BASE_URL")
    minimax_model: str = Field(default="MiniMax-M2.7", alias="MINIMAX_MODEL")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")

    mcd_mcp_base_url: str = Field(default="https://mcp.mcd.cn", alias="MCD_MCP_BASE_URL")
    mcd_mcp_token: str = Field(default="", alias="MCD_MCP_TOKEN")
    mcd_mcp_protocol_version: str = Field(default="2025-06-18", alias="MCD_MCP_PROTOCOL_VERSION")

    default_order_type: int = Field(default=2, alias="DEFAULT_ORDER_TYPE")

    session_store_path: Path = Field(default=Path(".agent_state/sessions"), alias="SESSION_STORE_PATH")
    log_dir: Path = Field(default=Path("logs"), alias="LOG_DIR")
    nutrition_catalog_path: Path = Field(
        default=Path("data/nutrition_catalog.sample.json"),
        alias="NUTRITION_CATALOG_PATH",
    )
    dry_run_orders: bool = Field(default=True, alias="DRY_RUN_ORDERS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
