"""Read-only inspection of one explicitly named accessible Telegram source."""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from telethon import functions
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    PhoneNumberInvalidError,
    SessionExpiredError,
    SessionRevokedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UserNotParticipantError,
)
from telethon.errors import RPCError
from telethon.tl.types import Channel, Chat

from telegramlab.extractors.links import contains_telegram_reference, extract_urls, is_external_url
from telegramlab.sources.registry import RegistryError, save_inspection_record

_DEFAULT_LIMIT = 30
_MAX_LIMIT = 100
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
_URI_PREFIX_RE = re.compile(r"(?i)(?<![a-z0-9+.-])(?:vless|ss)://")
_URI_REDACTION_RE = re.compile(r"(?i)(?<![a-z0-9+.-])(vless|ss)://[^\s<>\"']+")
_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "www.telegram.me"})


class InspectionError(RuntimeError):
    """Expected, user-facing inspection error."""


class InvalidSourceError(InspectionError):
    """The supplied username is invalid or does not identify a source."""


class InaccessibleSourceError(InspectionError):
    """The authenticated account cannot access the source history."""


class InvalidCredentialsError(InspectionError):
    """Telegram rejected the configured credentials or saved authorization."""


class NetworkInspectionError(InspectionError):
    """Telegram could not be reached or the request timed out."""


class FloodWaitInspectionError(InspectionError):
    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"Telegram requested a wait of {seconds} seconds; inspection stopped without retrying.")


@dataclass(frozen=True, slots=True)
class InspectionResult:
    source_identity: dict[str, Any]
    metadata: dict[str, Any]
    inspected_at: str
    limit: int
    findings: dict[str, Any]
    warnings: list[str]
    completion_status: str = "completed"
    registry_source_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_limit(limit: int) -> int:
    if not 1 <= limit <= _MAX_LIMIT:
        raise InspectionError(f"Inspection limit must be between 1 and {_MAX_LIMIT}.")
    return limit


def normalize_target(target: str) -> str:
    raw = target.strip()
    # Negative marked dialog IDs can address groups already present in the
    # authenticated session cache; they never trigger a join operation.
    if re.fullmatch(r"-100\d{5,}|-\d{1,20}", raw):
        return raw
    if raw.startswith("@"):
        username = raw[1:]
    elif _USERNAME_RE.fullmatch(raw):
        # Accept a bare public username too. This also makes canonical
        # usernames returned by normalize_target safe to normalize again.
        username = raw
    else:
        candidate = raw if "://" in raw else f"https://{raw}"
        try:
            parts = urlsplit(candidate)
        except ValueError:
            raise InvalidSourceError("Telegram target is malformed.") from None
        if (parts.hostname or "").casefold() not in _TELEGRAM_HOSTS:
            raise InvalidSourceError("Provide a Telegram public username such as @ExampleChannel or t.me/ExampleChannel.")
        segments = [segment for segment in parts.path.split("/") if segment]
        if len(segments) != 1:
            raise InvalidSourceError("Only public Telegram usernames are supported; invite links are not joined.")
        username = segments[0]
    if not _USERNAME_RE.fullmatch(username):
        raise InvalidSourceError("Telegram username must contain 5–32 letters, numbers, or underscores.")
    return username.casefold()


def _raise_mapped_error(error: BaseException) -> None:
    if isinstance(error, FloodWaitError):
        raise FloodWaitInspectionError(error.seconds) from None
    if isinstance(error, (UsernameInvalidError, UsernameNotOccupiedError)):
        raise InvalidSourceError("Telegram username is unknown or invalid.") from None
    if isinstance(error, (ChannelPrivateError, ChatAdminRequiredError, UserNotParticipantError)):
        raise InaccessibleSourceError("This source is inaccessible to the authenticated account.") from None
    if isinstance(error, (ApiIdInvalidError, AuthKeyUnregisteredError, SessionExpiredError,
                          SessionRevokedError, PhoneNumberInvalidError)):
        raise InvalidCredentialsError("Telegram rejected the API credentials or existing session authorization.") from None
    if isinstance(error, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        raise NetworkInspectionError("Telegram network request failed or timed out.") from None
    if isinstance(error, RPCError):
        raise InspectionError(f"Telegram request failed ({type(error).__name__}).") from None


def _channel_type(entity: object) -> str:
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
            return "group"
        if getattr(entity, "broadcast", False):
            return "channel"
    raise InvalidSourceError("The username does not identify a Telegram channel or group.")


def _restriction_metadata(entity: object) -> list[dict[str, str | None]] | None:
    reasons = getattr(entity, "restriction_reason", None)
    if reasons is None:
        return None
    return [
        {"platform": getattr(reason, "platform", None),
         "reason": getattr(reason, "reason", None),
         "text": getattr(reason, "text", None)}
        for reason in reasons
    ]


def _safe_description(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _URI_REDACTION_RE.sub(lambda match: f"[REDACTED {match.group(1).upper()} URI]", value)


async def _metadata(client: Any, entity: object, source_type: str) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {
        "telegram_id": str(getattr(entity, "id", "")) or None,
        "username": getattr(entity, "username", None),
        "title": getattr(entity, "title", None),
        "description": None,
        "source_type": source_type,
        "participant_count": getattr(entity, "participants_count", None),
        "linked_discussion": None,
        "verified": getattr(entity, "verified", None),
        "access_restrictions": _restriction_metadata(entity),
    }
    warnings: list[str] = []
    if isinstance(entity, Channel):
        try:
            full = await client(functions.channels.GetFullChannelRequest(entity))
            full_channel = getattr(full, "full_chat", None)
            if full_channel is not None:
                metadata["description"] = _safe_description(getattr(full_channel, "about", None))
                participants = getattr(full_channel, "participants_count", None)
                if participants is not None:
                    metadata["participant_count"] = participants
                linked_id = getattr(full_channel, "linked_chat_id", None)
                if linked_id is not None:
                    linked_entity = next(
                        (item for item in getattr(full, "chats", []) if getattr(item, "id", None) == linked_id),
                        None,
                    )
                    metadata["linked_discussion"] = {
                        "telegram_id": str(linked_id),
                        "username": getattr(linked_entity, "username", None),
                        "title": getattr(linked_entity, "title", None),
                    }
        except Exception as error:
            _raise_mapped_error(error)
            warnings.append(f"Some full-channel metadata was unavailable ({type(error).__name__}).")
    return metadata, warnings


def _date_value(message: Any) -> str | None:
    date = getattr(message, "date", None)
    if date is None:
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return date.isoformat()


async def inspect_source(
    client: Any,
    target: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    progress: Callable[[str], None] | None = print,
    registry_path: Any | None = None,
    save: bool = False,
) -> InspectionResult:
    """Inspect one supplied username or cached dialog ID without joining or persisting text."""
    limit = validate_limit(limit)
    username = normalize_target(target)
    report = progress or (lambda _message: None)
    report("[1/4] Resolving Telegram source")
    try:
        lookup = int(username) if username.startswith("-") else username
        entity = await client.get_entity(lookup)
    except Exception as error:
        _raise_mapped_error(error)
        if isinstance(error, ValueError):
            raise InvalidSourceError("Telegram username is unknown or invalid.") from None
        raise InspectionError(f"Could not resolve Telegram source ({type(error).__name__}).") from None

    source_type = _channel_type(entity)
    report("[2/4] Fetching available metadata")
    metadata, warnings = await _metadata(client, entity, source_type)

    report("[3/4] Inspecting recent messages")
    count = urls_count = telegram_refs = external_urls = vless = shadowsocks = 0
    dates: list[str] = []
    iterator = client.iter_messages(entity, limit=limit).__aiter__()
    try:
        while count < limit:
            try:
                message = await anext(iterator)
            except StopAsyncIteration:
                break
            count += 1
            text = getattr(message, "raw_text", None) or getattr(message, "message", None) or ""
            urls = extract_urls(text, getattr(message, "entities", None))
            if urls:
                urls_count += 1
            if contains_telegram_reference(text, urls):
                telegram_refs += 1
            if any(is_external_url(url) for url in urls):
                external_urls += 1
            prefixes = {match.group(0).casefold() for match in _URI_PREFIX_RE.finditer(text)}
            if "vless://" in prefixes:
                vless += 1
            if "ss://" in prefixes:
                shadowsocks += 1
            date_value = _date_value(message)
            if date_value:
                dates.append(date_value)
    except Exception as error:
        _raise_mapped_error(error)
        raise InspectionError(f"Could not inspect source messages ({type(error).__name__}).") from None
    finally:
        close_iterator = getattr(iterator, "aclose", None)
        if close_iterator is not None:
            try:
                await close_iterator()
            except Exception:
                pass

    inspected_at = datetime.now(UTC).isoformat(timespec="seconds")
    findings = {
        "messages_inspected": count,
        "messages_containing_urls": urls_count,
        "messages_containing_telegram_references": telegram_refs,
        "messages_containing_external_urls": external_urls,
        "messages_containing_vless_candidates": vless,
        "messages_containing_shadowsocks_candidates": shadowsocks,
        "earliest_inspected_message_date": min(dates) if dates else None,
        "latest_inspected_message_date": max(dates) if dates else None,
    }
    report("[4/4] Summarizing findings")
    result = InspectionResult(
        source_identity={"provider": "telegram", "source_type": source_type,
                         "identifier": ("@" + username if not username.startswith("-") else username),
                         "username": metadata["username"],
                         "telegram_id": metadata["telegram_id"]},
        metadata=metadata,
        inspected_at=inspected_at,
        limit=limit,
        findings=findings,
        warnings=warnings,
    )
    if save:
        if registry_path is None:
            raise InspectionError("Registry path is required when --save is used.")
        try:
            source_id = save_inspection_record(
                registry_path,
                identifier=("@" + str(metadata["username"]).casefold()
                            if metadata.get("username") else f"telegram-id:{username}"),
                source_type=source_type,
                metadata=metadata,
                inspected_at=inspected_at,
                limit=limit,
                findings=findings,
                warnings=warnings,
            )
        except RegistryError as error:
            raise InspectionError(str(error)) from None
        result = InspectionResult(**{**result.as_dict(), "registry_source_id": source_id})
    return result
