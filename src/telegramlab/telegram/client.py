"""Telethon client construction without interactive authentication."""

from pathlib import Path

from telethon import TelegramClient

from telegramlab.core.config import Settings


class MissingSessionError(FileNotFoundError):
    """The configured existing Telethon session file does not exist."""


def session_file_path(session_path: Path) -> Path:
    """Return the exact SQLite session file path Telethon will use."""
    return session_path if str(session_path).endswith(".session") else Path(f"{session_path}.session")


def require_existing_session(session_path: Path) -> Path:
    """Require an existing session before constructing Telethon's SQLite session."""
    session_file = session_file_path(session_path)
    if not session_file.is_file():
        raise MissingSessionError(f"Telegram session file is missing: {session_file}")
    return session_file


def create_client(settings: Settings) -> TelegramClient:
    """Construct a client against the already existing project session only."""
    require_existing_session(settings.session_path)
    if settings.api_id is None or not settings.api_hash:
        raise ValueError("TG_API_ID and TG_API_HASH are required for Telegram access")
    session_arg = str(settings.session_path)
    if settings.session_path.suffix == ".session":
        session_arg = str(settings.session_path.with_suffix(""))
    return TelegramClient(session_arg, settings.api_id, settings.api_hash)
