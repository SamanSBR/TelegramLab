"""Unified, bounded research over existing TelegramLab services."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from telegramlab.core.config import PROJECT_ROOT, Settings
from telegramlab.sources.registry import RegistryError
from telegramlab.telegram.content import ContentHit, normalize_text, search_content
from telegramlab.telegram.explorer import explore_source
from telegramlab.telegram.inspector import FloodWaitInspectionError, InspectionError, inspect_source
from telegramlab.telegram.search import SearchResult, search_public, validate_search_query


class ResearchError(InspectionError):
    """A research workflow cannot run with the requested scope."""


@dataclass(slots=True)
class ResearchSource:
    telegram_id: str | None
    username: str | None
    entity_type: str
    display_name: str | None
    discovery_evidence: list[dict[str, Any]] = field(default_factory=list)
    inspected: bool = False
    inspection: dict[str, Any] | None = None
    exploration: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResearchResponse:
    query: str
    started_at: str
    finished_at: str
    mode: str
    limit: int
    max_sources: int
    options: dict[str, bool]
    sources: list[ResearchSource]
    content_results: list[ContentHit]
    evidence_summary: dict[str, int]
    warnings: list[str]
    errors: list[str]
    completion_status: str
    workflow_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _history_path(path: Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    settings = Settings.from_env()
    protected = {settings.database_path.resolve(), settings.sources_database_path.resolve(),
                 settings.content_database_path.resolve(),
                 (PROJECT_ROOT / "telegram_lab.db").resolve(), (PROJECT_ROOT / "data/sources.db").resolve(),
                 (PROJECT_ROOT / "telegram_lab.session").resolve()}
    if resolved in protected or resolved.suffix.casefold() == ".session":
        raise ResearchError("Research history requires a separate SQLite file.")
    return resolved


def save_history(path: Path, result: ResearchResponse) -> int:
    path = _history_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS research_runs (
            id INTEGER PRIMARY KEY, query TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL, status TEXT NOT NULL, options_json TEXT NOT NULL,
            result_json TEXT NOT NULL)""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_research_runs_time ON research_runs(started_at)")
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1, ?)", (_now(),))
        cursor = db.execute("""INSERT INTO research_runs
            (query, started_at, finished_at, status, options_json, result_json)
            VALUES (?, ?, ?, ?, ?, ?)""", (
            result.query, result.started_at, result.finished_at, result.completion_status,
            json.dumps(result.options, ensure_ascii=False),
            json.dumps(result.as_dict(), ensure_ascii=False)))
        return int(cursor.lastrowid)


def read_history(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    path = _history_path(path)
    if not 1 <= limit <= 100:
        raise ResearchError("History limit must be between 1 and 100.")
    if not path.is_file():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='research_runs'").fetchone():
            return []
        rows = db.execute("SELECT id, result_json FROM research_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**json.loads(row[1]), "workflow_id": row[0]} for row in rows]


def _deduplicate(results: list[SearchResult], limit: int) -> list[ResearchSource]:
    found: list[ResearchSource] = []
    for item in results:
        if item.provider != "telegram" or item.entity_type not in {"channel", "group"} or not item.username:
            continue
        source = next((existing for existing in found
                       if existing.entity_type == item.entity_type and
                       ((existing.telegram_id and item.telegram_id and
                         existing.telegram_id == item.telegram_id) or
                        ((not existing.telegram_id or not item.telegram_id) and
                         existing.username and existing.username.casefold() == item.username.casefold()))), None)
        if source is None:
            source = ResearchSource(item.telegram_id, item.username, item.entity_type, item.display_name)
            found.append(source)
        elif source.telegram_id is None:
            source.telegram_id = item.telegram_id
        evidence = {"search_query": item.search_query, "discovery_method": item.discovery_method,
                    "discovered_at": item.discovered_at, "origin": item.origin,
                    "username": item.username, "telegram_id": item.telegram_id,
                    "registry_source_id": item.registry_source_id}
        if evidence not in source.discovery_evidence:
            source.discovery_evidence.append(evidence)
    return found[:limit]


def _unique_content(items: list[ContentHit]) -> list[ContentHit]:
    found: dict[tuple[str, int], ContentHit] = {}
    for item in items:
        found.setdefault((item.source_id, item.message_id), item)
    return list(found.values())


def _local_discovery_matches(path: Path, query: str) -> list[SearchResult]:
    """Read saved source-query provenance without creating or migrating the registry."""
    with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='search_discoveries'").fetchone():
            return []
        rows = db.execute("""SELECT s.id AS registry_id, s.platform_id, s.identifier,
            s.display_name, s.source_type, d.search_query, d.discovery_method, d.discovered_at
            FROM search_discoveries AS d JOIN sources AS s ON s.id=d.source_id
            WHERE s.provider='telegram' AND s.source_type IN ('channel','group')
            ORDER BY d.id""")
        matches = []
        for row in rows:
            if normalize_text(row["search_query"]) != normalize_text(query):
                continue
            identifier = row["identifier"]
            if not identifier.startswith("@"):
                continue
            matches.append(SearchResult(
                "telegram", row["platform_id"], identifier[1:], row["display_name"],
                row["source_type"], None, None, None, row["search_query"],
                row["discovery_method"], row["discovered_at"], True, row["registry_id"],
                origin="local"))
        return matches


async def run_research(client: Any | None, query: str, *, limit: int = 20,
                       max_sources: int = 3, inspect: bool = False, explore: bool = False,
                       content: bool = False, local: bool = False, save: bool = False,
                       registry_path: Path, content_path: Path, history_path: Path,
                       progress: Callable[[str], None] | None = None) -> ResearchResponse:
    """Run one source request, optional bounded source reads, and optional content request."""
    query = validate_search_query(query, "local" if local else "telegram", "all", limit)
    if not 1 <= max_sources <= 10:
        raise ResearchError("--max-sources must be between 1 and 10.")
    if local and (inspect or explore):
        raise ResearchError("--inspect and --explore require a live public source search.")
    if not local and client is None:
        raise ResearchError("Live research requires an existing authorized Telegram session.")
    if not Path(registry_path).is_file():
        raise ResearchError("Source registry is missing; research will not recreate it.")
    if save:
        _history_path(history_path)
    report = progress or (lambda _: None)
    started = _now()
    warnings: list[str] = []
    errors: list[str] = []
    options = {"inspect": inspect, "explore": explore, "content": content, "local": local, "save": save}
    report("[1/5] Searching local source evidence" if local else "[1/5] Searching public sources")
    try:
        found = await search_public(client, query, mode="local" if local else "telegram",
                                    entity_type="all", limit=limit, registry_path=registry_path,
                                    save=save and not local, progress=None)
    except (InspectionError, RegistryError) as error:
        response = ResearchResponse(
            query, started, _now(), "local" if local else "telegram", limit, max_sources,
            options, [], [], {"sources_discovered": 0, "sources_inspected": 0,
                              "sources_explored": 0, "messages_observed": 0,
                              "vless_candidates": 0, "shadowsocks_candidates": 0,
                              "unique_references": 0, "reference_occurrences": 0,
                              "content_results": 0}, [], [str(error)], "failed")
        if save:
            response.workflow_id = save_history(history_path, response)
        return response
    local_evidence = _local_discovery_matches(registry_path, query) if local else []
    sources = _deduplicate(found.results + local_evidence, limit)
    warnings.extend(found.warnings)
    if not sources:
        warnings.append("No source results were returned; this does not prove the content is absent.")
    report(f"[2/5] Normalized {len(sources)} unique sources")
    selected = [source for source in sources if source.telegram_id][:max_sources] if not local else []
    if (inspect or explore) and len(sources) > len(selected):
        warnings.append(f"Only the first {len(selected)} discovered sources were selected for inspection or exploration.")
    stopped_for_flood = False
    if inspect:
        for number, source in enumerate(selected, 1):
            target = "@" + str(source.username)
            report(f"[3/5] Inspecting discovered source {number}/{len(selected)}")
            try:
                inspected = await inspect_source(
                    client, target, limit=limit, progress=None, registry_path=registry_path,
                    save=save, expected_id=source.telegram_id)
                source.inspection = inspected.as_dict()
                source.inspected = True
                warnings.extend(inspected.warnings)
            except FloodWaitInspectionError as error:
                errors.append(f"{target}: {error}")
                stopped_for_flood = True
                break
            except (InspectionError, RegistryError) as error:
                source.errors.append(f"Inspection: {error}")
                errors.append(f"{target}: inspection failed ({error})")
        if not selected:
            report("[3/5] No discovered sources available for inspection")
    else:
        report("[3/5] Inspection skipped")
    if explore and not stopped_for_flood:
        for number, source in enumerate(selected, 1):
            target = "@" + str(source.username)
            report(f"[4/5] Exploring discovered source {number}/{len(selected)}")
            try:
                explored = await explore_source(
                    client, target, limit=limit, progress=None, registry_path=registry_path,
                    save=save, expected_id=source.telegram_id)
                source.exploration = explored.as_dict()
                warnings.extend(explored.warnings)
            except FloodWaitInspectionError as error:
                errors.append(f"{target}: {error}")
                stopped_for_flood = True
                break
            except (InspectionError, RegistryError) as error:
                source.errors.append(f"Exploration: {error}")
                errors.append(f"{target}: exploration failed ({error})")
        if not selected:
            report("[4/5] No discovered sources available for exploration")
    else:
        report("[4/5] Exploration stopped for FloodWait" if stopped_for_flood else "[4/5] Exploration skipped")
    content_hits: list[ContentHit] = []
    if (content or local) and not stopped_for_flood:
        report("[5/5] Reviewing local content evidence" if local else "[5/5] Searching accessible public content")
        try:
            result = await search_content(client, query, kind="all", limit=limit,
                                          local=local, save=save and not local,
                                          index_path=content_path, progress=None)
            content_hits = _unique_content(result.results)
            warnings.extend(result.warnings)
        except FloodWaitInspectionError as error:
            errors.append(str(error))
            stopped_for_flood = True
        except (InspectionError, sqlite3.DatabaseError) as error:
            errors.append(f"Content search failed ({error})")
    else:
        report("[5/5] Content search stopped for FloodWait" if stopped_for_flood else "[5/5] Content search skipped")
    summary = {
        "sources_discovered": len(sources),
        "sources_inspected": sum(source.inspected for source in sources),
        "sources_explored": sum(source.exploration is not None for source in sources),
        "messages_observed": sum(source.inspection["findings"]["messages_inspected"] for source in sources if source.inspection),
        "vless_candidates": sum(source.inspection["findings"]["messages_containing_vless_candidates"] for source in sources if source.inspection),
        "shadowsocks_candidates": sum(source.inspection["findings"]["messages_containing_shadowsocks_candidates"] for source in sources if source.inspection),
        "unique_references": sum(source.exploration["unique_references"] for source in sources if source.exploration),
        "reference_occurrences": sum(source.exploration["total_occurrences"] for source in sources if source.exploration),
        "content_results": len(content_hits),
    }
    response = ResearchResponse(query, started, _now(), "local" if local else "telegram", limit,
                                max_sources, options, sources, content_hits, summary, warnings, errors,
                                "partial" if errors else "completed")
    if save:
        try:
            response.workflow_id = save_history(history_path, response)
        except (sqlite3.DatabaseError, OSError) as error:
            raise ResearchError(f"Research history could not be saved ({type(error).__name__}).") from None
    return response
