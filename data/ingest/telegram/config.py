from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SESSION_DIR = ROOT_DIR / "data" / "ingest" / "telegram" / "sessions"
DEFAULT_EXPORT_DIR = ROOT_DIR / "data" / "ingest" / "telegram" / "exports"


class TelegramSettings(BaseSettings):
    api_id: int = Field(alias="TELEGRAM_API_ID")
    api_hash: str = Field(alias="TELEGRAM_API_HASH")
    app_title: str = Field(default="KERNEL", alias="TELEGRAM_APP_TITLE")
    app_short_name: str = Field(default="kernelapp2025", alias="TELEGRAM_APP_SHORT_NAME")
    phone: str | None = Field(default=None, alias="TELEGRAM_PHONE")
    session_string: str | None = Field(default=None, alias="TELEGRAM_SESSION_STRING")
    session_name: str = Field(default="stoic_user", alias="TELEGRAM_SESSION_NAME")
    session_dir: Path = Field(default=DEFAULT_SESSION_DIR, alias="TELEGRAM_SESSION_DIR")
    export_dir: Path = Field(default=DEFAULT_EXPORT_DIR, alias="TELEGRAM_EXPORT_DIR")

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def session_path(self) -> Path:
        return self.session_dir / self.session_name

    def ensure_directories(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_telegram_settings() -> TelegramSettings:
    settings = TelegramSettings()
    settings.ensure_directories()
    return settings


def persist_telegram_env(updates: dict[str, str | None]) -> None:
    env_path = ROOT_DIR / ".env"
    env_path.touch(exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines()
    pending = dict(updates)
    next_lines: list[str] = []

    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            next_lines.append(line)
            continue

        key, _, _ = line.partition("=")
        key = key.strip()
        if key in pending:
            value = pending.pop(key)
            next_lines.append(f"{key}={value or ''}")
        else:
            next_lines.append(line)

    for key, value in pending.items():
        next_lines.append(f"{key}={value or ''}")

    env_path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")
    get_telegram_settings.cache_clear()


def delete_legacy_session_file(settings: TelegramSettings) -> None:
    session_file = settings.session_path.with_suffix(".session")
    session_journal = settings.session_path.with_suffix(".session-journal")
    for path in (session_file, session_journal):
        if path.exists():
            try:
                path.unlink()
            except PermissionError:
                continue
