"""Telethon client construction, preserving the legacy session basename."""

from telethon import TelegramClient

from telegramlab.core.config import Settings


def create_client(settings: Settings) -> TelegramClient:
    """Create a client using the existing ``telegram_lab`` session by default."""
    if settings.api_id is None or not settings.api_hash:
        raise ValueError("TG_API_ID and TG_API_HASH are required for Telegram access")
    return TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)
