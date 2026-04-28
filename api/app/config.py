from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    database_schema: str = Field(default="kb", alias="DATABASE_SCHEMA")
    stoic_api_key: SecretStr = Field(alias="STOIC_API_KEY")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    codex_command: str = Field(default="codex", alias="CODEX_COMMAND")
    codex_home: str = Field(
        default_factory=lambda: os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
        alias="CODEX_HOME",
    )
    codex_workdir: str = Field(default=str(ROOT_DIR), alias="CODEX_WORKDIR")
    codex_default_model: str = Field(default="gpt-5.2", alias="CODEX_DEFAULT_MODEL")
    codex_models: str = Field(
        default="gpt-5.2,gpt-5.3-codex,gpt-5.4,gpt-5.4-mini,gpt-5.5",
        alias="CODEX_MODELS",
    )
    codex_timeout_seconds: int = Field(default=600, alias="CODEX_TIMEOUT_SECONDS")
    codex_default_reasoning_effort: str = Field(default="medium", alias="CODEX_DEFAULT_REASONING_EFFORT")
    codex_update_check_interval_seconds: int = Field(default=3600, alias="CODEX_UPDATE_CHECK_INTERVAL_SECONDS")

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def codex_model_list(self) -> list[str]:
        return [model.strip() for model in self.codex_models.split(",") if model.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
