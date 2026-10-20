"""Bounded public chat discovery and offline registry search."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from telethon import functions
from telethon.tl.types import Channel, Chat, User

from telegramlab.sources.registry import RegistryError, Source, list_sources, save_search_candidates
from telegramlab.telegram.inspector import InspectionError, _raise_mapped_error, normalize_target


class SearchError(InspectionError):
    """Invalid or failed public search."""


@dataclass(slots=True)
class SearchResult:
    provider: str
    telegram_id: str | None
    username: str | None
    display_name: str | None
    entity_type: str
    description: str | None
    participant_count: int | None
    verified: bool | None
    search_query: str
    discovery_method: str
    discovered_at: str
    already_registered: bool = False
    registry_source_id: int | None = None
    warnings: list[str] = field(default_factory=list)
    origin: str = "telegram"


@dataclass(slots=True)
class SearchResponse:
    query: str
    mode: str
    entity_type: str
    limit: int
    results: list[SearchResult]
    warnings: list[str]
    telegram_returned: int
    local_found: int
    candidates_saved: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _kind(entity: object) -> str | None:
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
            return "group"
        if getattr(entity, "broadcast", False):
            return "channel"
    return None


def _record(entity: object, query: str, method: str, timestamp: str) -> SearchResult | None:
    kind = _kind(entity)
    if kind is None:
        return None
    username = getattr(entity, "username", None)
    if kind == "user":
        display = " ".join(filter(None, (getattr(entity, "first_name", None),
                                          getattr(entity, "last_name", None)))) or None
    else:
        display = getattr(entity, "title", None)
    number = getattr(entity, "id", None)
    return SearchResult("telegram", str(number) if number is not None else None,
                        username.casefold() if username else None, display, kind, None,
                        getattr(entity, "participants_count", None),
                        getattr(entity, "verified", None), query, method, timestamp)


def _local_record(source: Source, query: str) -> SearchResult:
    username = source.identifier[1:].casefold() if source.provider == "telegram" and source.identifier.startswith("@") else None
    return SearchResult(source.provider, source.platform_id if source.provider == "telegram" else None,
                        username, source.display_name, source.source_type, source.description,
                        None, None, query, source.discovery_method, source.discovered_at,
                        True, source.id, origin="local")


def _matches(source: Source, query: str) -> bool:
    term = query.casefold()
    return any(term in (value or "").casefold() for value in (
        source.display_name, source.identifier, source.description,
        source.research_category, source.provider))


def _merge(results: list[SearchResult]) -> list[SearchResult]:
    merged: list[SearchResult] = []
    for result in results:
        matches: list[SearchResult] = []
        for existing in merged:
            if result.provider != existing.provider or result.entity_type != existing.entity_type:
                continue
            if result.telegram_id and existing.telegram_id:
                same = result.telegram_id == existing.telegram_id
            else:
                same = bool(result.username and existing.username and
                            result.username.casefold() == existing.username.casefold())
            if same:
                matches.append(existing)
        if len(matches) != 1:
            if matches:
                result.warnings.append("Username matches multiple Telegram IDs; identity is ambiguous.")
            merged.append(result)
        else:
            match = matches[0]
            match.already_registered |= result.already_registered
            match.registry_source_id = match.registry_source_id or result.registry_source_id
            match.origin = "both" if match.origin != result.origin else match.origin
            for attr in ("telegram_id", "username", "display_name", "description", "participant_count", "verified"):
                if getattr(match, attr) is None:
                    setattr(match, attr, getattr(result, attr))
    return merged


def _annotate_registry(results: list[SearchResult], sources: list[Source]) -> None:
    for result in results:
        if result.provider != "telegram" or result.already_registered or result.warnings:
            continue
        candidates = [source for source in sources if source.provider == "telegram"]
        same_id = [source for source in candidates if result.telegram_id and source.platform_id == result.telegram_id]
        same_name = [source for source in candidates if result.username and
                     source.identifier.casefold() == "@" + result.username.casefold()]
        conflicts = [source for source in same_id if source.source_type != result.entity_type]
        conflicts += [source for source in same_name if
                     (source.platform_id and result.telegram_id and source.platform_id != result.telegram_id)
                     or source.source_type != result.entity_type]
        if conflicts:
            result.warnings.append("Username conflicts with a different registered Telegram ID or entity type; candidate was not saved.")
            continue
        matches = [source for source in same_id + same_name if source.source_type == result.entity_type]
        if matches:
            result.already_registered = True
            result.registry_source_id = matches[0].id


async def search_public(client: Any | None, query: str, *, mode: str = "telegram",
                        entity_type: str = "all", limit: int = 20, registry_path: Path,
                        save: bool = False, progress: Callable[[str], None] | None = None) -> SearchResponse:
    """Search one literal query with at most one Telegram discovery request."""
    query = validate_search_query(query, mode, entity_type, limit)
    if mode != "local" and client is None:
        raise SearchError("Telegram search requires an existing authorized session.")
    report = progress or (lambda _: None)
    report("[1/3] Preparing search query" if mode == "local" else
           "[1/4] Preparing search query")
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    exact = query.startswith("@") or bool(re.match(r"(?i)^(?:https?://)?(?:t\.me|telegram\.me)/", query))
    telegram: list[SearchResult] = []
    warnings: list[str] = []
    if mode != "local":
        report("[2/4] Resolving public username" if exact else
               "[2/4] Searching Telegram public index")
        try:
            if exact:
                username = normalize_target(query)
                entity = await client.get_entity(username)
                record = _record(entity, query, "exact_username", timestamp)
                if record:
                    telegram.append(record)
            else:
                response = await client(functions.contacts.SearchRequest(q=query, limit=limit))
                telegram = [record for entity in getattr(response, "chats", [])
                            if getattr(entity, "username", None)
                            if (record := _record(entity, query, "telegram_public_search", timestamp))]
            warnings.append("Exact username resolution checks one entity only." if exact else
                            "Telegram public search is limited by its index and may omit matching chats.")
        except Exception as error:
            _raise_mapped_error(error)
            if isinstance(error, InspectionError):
                raise
            raise SearchError(f"Telegram search failed ({type(error).__name__}).") from None
    if mode == "local":
        report("[2/3] Searching local source registry")
    else:
        report("[3/4] Normalizing and deduplicating results")
    telegram = _merge(telegram)
    if mode != "local":
        report("[4/4] Checking source registry")
    sources = list_sources(registry_path)
    local = [_local_record(source, query) for source in sources if _matches(source, query)] if mode != "telegram" else []
    if mode == "local":
        report("[3/3] Normalizing and deduplicating results")
    if entity_type != "all":
        telegram = [item for item in telegram if item.entity_type == entity_type]
        local = [item for item in local if item.entity_type == entity_type]
    combined = _merge(telegram + local)
    _annotate_registry(combined, sources)
    combined = combined[:limit]
    saved = 0
    if save:
        eligible = [item for item in combined if item.provider == "telegram" and item.origin != "local" and
                    item.entity_type in {"channel", "group"} and item.username and
                    not item.warnings]
        outcome = save_search_candidates(registry_path, eligible)
        saved = outcome["created"]
        for item in eligible:
            item.registry_source_id = outcome["ids"].get((item.entity_type, item.username))
    return SearchResponse(query, mode, entity_type, limit, combined, warnings,
                          len(telegram), len(local), saved)


def validate_search_query(query: str, mode: str, entity_type: str, limit: int) -> str:
    query = query.strip()
    if not query or len(query) > 256 or any(ord(char) < 32 for char in query):
        raise SearchError("Search query must be 1–256 printable characters.")
    if mode not in {"telegram", "local", "both"} or entity_type not in {"channel", "group", "all"}:
        raise SearchError("Invalid search mode or entity type.")
    if not 1 <= limit <= 100:
        raise SearchError("Search limit must be between 1 and 100.")
    return query
