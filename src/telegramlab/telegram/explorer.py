"""Read-only, bounded exploration of references from one selected source."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from telegramlab.research.references import ReferenceOccurrence, extract_message_references
from telegramlab.sources.registry import RegistryError, save_reference_discoveries
from telegramlab.telegram.inspector import (
    InspectionError,
    _channel_type,
    _raise_mapped_error,
    normalize_target,
)

_DEFAULT_LIMIT = 30
_MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ExplorationResult:
    source_identity: dict[str, Any]
    explored_at: str
    limit: int
    messages_inspected: int
    unique_references: int
    total_occurrences: int
    references: list[dict[str, Any]]
    warnings: list[str]
    completion_status: str = "completed"
    registry_summary: dict[str, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _observation_dict(item: ReferenceOccurrence) -> dict[str, object]:
    return item.as_dict()


async def explore_source(
    client: Any,
    target: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    progress: Callable[[str], None] | None = print,
    registry_path: Any | None = None,
    save: bool = False,
) -> ExplorationResult:
    """Explore one explicitly selected accessible channel/group, without following links."""
    if not 1 <= limit <= _MAX_LIMIT:
        raise InspectionError(f"Exploration limit must be between 1 and {_MAX_LIMIT}.")
    normalized_target = normalize_target(target)
    report = progress or (lambda _message: None)
    report("[1/4] Resolving Telegram source")
    lookup: str | int = int(normalized_target) if normalized_target.startswith("-") else normalized_target
    try:
        entity = await client.get_entity(lookup)
    except Exception as error:
        _raise_mapped_error(error)
        if isinstance(error, ValueError):
            raise InspectionError("Telegram source is unknown or invalid.") from None
        raise InspectionError(f"Could not resolve Telegram source ({type(error).__name__}).") from None
    source_type = _channel_type(entity)
    source_id = str(getattr(entity, "id", ""))
    if not source_id.isdigit():
        raise InspectionError("Telegram did not provide a numeric source ID.")
    username = getattr(entity, "username", None)
    title = getattr(entity, "title", None)
    report("[2/4] Fetching recent accessible messages")

    messages: list[tuple[int, str, list[object]]] = []
    iterator = client.iter_messages(entity, limit=limit).__aiter__()
    try:
        while len(messages) < limit:
            try:
                message = await anext(iterator)
            except StopAsyncIteration:
                break
            message_id = getattr(message, "id", None)
            if message_id is None:
                continue
            text = getattr(message, "raw_text", None) or getattr(message, "message", None) or ""
            messages.append((int(message_id), text, list(getattr(message, "entities", None) or [])))
    except Exception as error:
        _raise_mapped_error(error)
        raise InspectionError(f"Could not read recent messages ({type(error).__name__}).") from None
    finally:
        close_iterator = getattr(iterator, "aclose", None)
        if close_iterator is not None:
            try:
                await close_iterator()
            except Exception:
                pass

    report("[3/4] Extracting and normalizing references")
    explored_at = _now()
    observations: list[ReferenceOccurrence] = []
    omitted_invites = 0
    for message_id, text, entities in messages:
        found, omitted = extract_message_references(
            text, entities, origin_message_id=message_id, discovered_at=explored_at
        )
        observations.extend(found)
        omitted_invites += omitted
    messages_inspected = len(messages)
    messages.clear()

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in observations:
        key = (item.reference_type, item.normalized_target)
        record = grouped.setdefault(key, {
            "reference_type": item.reference_type,
            "normalized_target": item.normalized_target,
            "occurrence_count": 0,
            "provenance": [],
            "candidate_provider": item.candidate_provider,
            "candidate_source_type": item.candidate_source_type,
            "candidate_identifier": item.candidate_identifier,
        })
        record["occurrence_count"] += item.occurrence_count
        record["provenance"].append({
            "origin_source_id": source_id,
            "origin_message_id": item.origin_message_id,
            "discovered_at": item.discovered_at,
            "occurrence_count": item.occurrence_count,
        })
    references = [grouped[key] for key in sorted(grouped)]
    warnings = ["Private Telegram invite links were omitted." ] if omitted_invites else []
    report("[4/4] Summarizing and optionally saving candidates")

    registry_summary: dict[str, int] | None = None
    if save:
        if registry_path is None:
            raise InspectionError("Registry path is required when --save is used.")
        origin_identifier = (
            "@" + username.casefold() if username
            else f"telegram-id:{normalized_target if normalized_target.startswith('-') else source_id}"
        )
        try:
            registry_summary = save_reference_discoveries(
                registry_path,
                origin_source_id=source_id,
                origin_identifier=origin_identifier,
                origin_source_type=source_type,
                origin_title=title,
                discoveries=[_observation_dict(item) for item in observations],
            )
        except RegistryError as error:
            raise InspectionError(str(error)) from None

    return ExplorationResult(
        source_identity={
            "provider": "telegram",
            "telegram_id": source_id,
            "username": username,
            "title": title,
            "source_type": source_type,
        },
        explored_at=explored_at,
        limit=limit,
        messages_inspected=messages_inspected,
        unique_references=len(references),
        total_occurrences=sum(item["occurrence_count"] for item in references),
        references=references,
        warnings=warnings,
        registry_summary=registry_summary,
    )
