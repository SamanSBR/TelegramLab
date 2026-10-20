"""Safe URL and Telegram-reference extraction from transient message text."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_TELEGRAM_MENTION_RE = re.compile(r"(?<![\w])@[A-Za-z0-9_]{5,32}\b")
_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "www.telegram.me"})


def _url_key(url: str) -> str:
    try:
        parts = urlsplit(url if "://" in url else f"https://{url}")
        parsed_port = parts.port
    except ValueError:
        return url.casefold()
    host = (parts.hostname or "").casefold()
    port = f":{parsed_port}" if parsed_port else ""
    return urlunsplit((parts.scheme.casefold(), host + port, parts.path, parts.query, ""))


def _clean_url(url: str) -> str:
    return url.strip().rstrip(".,;:!?")


def _utf16_slice(text: str, offset: int, length: int) -> str:
    encoded = text.encode("utf-16-le")
    return encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le", errors="ignore")


def extract_urls(text: str | None, entities: list[object] | None = None) -> list[str]:
    """Extract and deduplicate URLs, including Telegram's UTF-16 entities."""
    text = text or ""
    found: dict[str, str] = {}
    for match in _URL_RE.finditer(text):
        url = _clean_url(match.group(0))
        if url:
            found.setdefault(_url_key(url), url)
    for entity in entities or []:
        if isinstance(entity, MessageEntityTextUrl):
            url = _clean_url(entity.url)
        elif isinstance(entity, MessageEntityUrl):
            url = _clean_url(_utf16_slice(text, entity.offset, entity.length))
        else:
            continue
        if url:
            found.setdefault(_url_key(url), url)
    return list(found.values())


def contains_telegram_reference(text: str | None, urls: list[str]) -> bool:
    """Return whether text contains a t.me link or a Telegram @username."""
    if _TELEGRAM_MENTION_RE.search(text or ""):
        return True
    for url in urls:
        parts = urlsplit(url if "://" in url else f"https://{url}")
        if parts.scheme.casefold() == "tg" or (parts.hostname or "").casefold() in _TELEGRAM_HOSTS:
            return True
    return False


def is_external_url(url: str) -> bool:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if parts.scheme.casefold() == "tg":
        return False
    return (parts.hostname or "").casefold() not in _TELEGRAM_HOSTS
