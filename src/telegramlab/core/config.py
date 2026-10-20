"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
        root = Path.cwd()
        raw_api_id = os.environ.get("TG_API_ID")
        raw_api_hash = os.environ.get("TG_API_HASH")
        return cls(
            database_path=Path(os.environ.get("TELEGRAMLAB_DB_PATH", root / "telegram_lab.db")).expanduser(),
            sources_database_path=Path(
                os.environ.get("TELEGRAMLAB_SOURCES_DB_PATH", root / "data" / "sources.db")
            ).expanduser(),
            session_path=Path(os.environ.get("TELEGRAMLAB_SESSION_PATH", root / "telegram_lab")).expanduser(),
            api_id=int(raw_api_id) if raw_api_id else None,
            api_hash=raw_api_hash or None,
        )
