"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _project_path(raw_value: str | None, default: Path) -> Path:
    path = Path(raw_value).expanduser() if raw_value else default
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime paths and optional Telegram credentials."""

    database_path: Path
    sources_database_path: Path
    session_path: Path
    api_id: int | None = None
    api_hash: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        """Read settings without requiring credentials for offline commands."""
        raw_api_id = os.environ.get("TG_API_ID")
        raw_api_hash = os.environ.get("TG_API_HASH")
        return cls(
            database_path=_project_path(os.environ.get("TELEGRAMLAB_DB_PATH"), PROJECT_ROOT / "telegram_lab.db"),
            sources_database_path=_project_path(
                os.environ.get("TELEGRAMLAB_SOURCES_DB_PATH"), PROJECT_ROOT / "data" / "sources.db"
            ),
            session_path=_project_path(os.environ.get("TELEGRAMLAB_SESSION_PATH"), PROJECT_ROOT / "telegram_lab"),
            api_id=int(raw_api_id) if raw_api_id else None,
            api_hash=raw_api_hash or None,
        )
