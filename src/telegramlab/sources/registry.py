"""Persistent, offline registry for research sources and evidenced relationships."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

STATUSES = frozenset({"candidate", "active", "paused", "inaccessible", "rejected"})
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "www.telegram.me"})


class RegistryError(ValueError):
    """Expected user-facing registry validation or lookup error."""


@dataclass(frozen=True, slots=True)
class Source:
    id: int
    provider: str
    source_type: str
    identifier: str
    canonical_url: str | None
    display_name: str | None
    description: str | None
    discovery_method: str
    discovered_from: str | None
    discovered_at: str
    last_checked_at: str | None
    status: str
    research_category: str
    notes: str | None
    created_at: str
    updated_at: str
    platform_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRelationship:
    id: int
    source_id: int
    related_source_id: int
    relationship_type: str
    discovered_at: str
    evidence_reference: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slug(value: str, field: str) -> str:
    normalized = value.strip().casefold()
    if not normalized or not _SLUG_RE.fullmatch(normalized):
        raise RegistryError(f"{field} must be a non-empty slug using letters, numbers, '.', '_' or '-'.")
    return normalized


def _canonical_web_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise RegistryError("URL cannot be empty.")
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError:
        raise RegistryError("URL is malformed.") from None
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise RegistryError("URL must be a valid HTTP or HTTPS URL.")
    if parts.username or parts.password:
        raise RegistryError("URLs containing credentials are not accepted.")
    host = parts.hostname.casefold()
    port_suffix = f":{port}" if port else ""
    netloc = host + port_suffix
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.casefold(), netloc, path, parts.query, ""))


def normalize_telegram_identifier(identifier: str) -> tuple[str, str | None, str]:
    """Return stable username identity, canonical URL, and uniqueness key.

    Only public usernames are case-folded. Invite links and other Telegram URLs
    retain case-sensitive path data and are not treated as username aliases.
    """
    raw = identifier.strip()
    if not raw:
        raise RegistryError("identifier cannot be empty.")
    candidate = raw
    if raw.startswith("@"):
        username = raw[1:]
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            raise RegistryError("Telegram usernames must contain 5–32 letters, numbers, or underscores.")
        canonical_id = "@" + username.casefold()
        return canonical_id, f"https://t.me/{username.casefold()}", canonical_id
    if "://" not in candidate and not candidate.startswith("//"):
        candidate = "https://" + candidate
    parts = urlsplit(candidate)
    host = (parts.hostname or "").casefold()
    if host in _TELEGRAM_HOSTS:
        segments = [segment for segment in parts.path.split("/") if segment]
        if len(segments) == 1 and re.fullmatch(r"[A-Za-z0-9_]{5,32}", segments[0]):
            username = segments[0].casefold()
            canonical_id = "@" + username
            return canonical_id, f"https://t.me/{username}", canonical_id
        canonical = _canonical_web_url(candidate)
        # Invite codes and message paths are deliberately not case-folded.
        return canonical, canonical, canonical
    # Bare Telegram username is accepted as a convenience.
    if re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", raw):
        username = raw.lstrip("@").casefold()
        canonical_id = "@" + username
        return canonical_id, f"https://t.me/{username}", canonical_id
    canonical = _canonical_web_url(candidate)
    return canonical, canonical, canonical


def _normalize_identity(provider: str, identifier: str) -> tuple[str, str | None, str]:
    if provider == "telegram":
        if identifier.casefold().startswith("tg://"):
            normalized = identifier.strip()
            return normalized, None, normalized
        if identifier.startswith("telegram-id:"):
            numeric_id = identifier.removeprefix("telegram-id:")
            if not re.fullmatch(r"-?\d{1,20}", numeric_id):
                raise RegistryError("Telegram numeric identifiers must contain only a signed numeric ID.")
            return identifier, None, identifier
        return normalize_telegram_identifier(identifier)
    raw = identifier.strip()
    if not raw:
        raise RegistryError("identifier cannot be empty.")
    if "://" in raw or re.match(r"^(?:www\.)?[^/@\s]+\.[^/@\s]+(?:/|$)", raw):
        canonical = _canonical_web_url(raw)
        return canonical, canonical, canonical
    return raw, None, raw


def initialize_database(path: Path) -> None:
    """Create registry schema at a dedicated path; never migrates other DBs."""
    path = Path(path)
    protected_paths = {
        (Path.cwd() / "telegram_lab.db").resolve(),
        Path(os.environ.get("TELEGRAMLAB_DB_PATH", Path.cwd() / "telegram_lab.db")).expanduser().resolve(),
    }
    if path.expanduser().resolve() in protected_paths:
        raise RegistryError("The source registry must use a separate database from the existing TelegramLab database.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                source_type TEXT NOT NULL,
                identifier TEXT NOT NULL,
                identity_key TEXT NOT NULL,
                canonical_url TEXT,
                display_name TEXT,
                description TEXT,
                discovery_method TEXT NOT NULL,
                discovered_from TEXT,
                discovered_at TEXT NOT NULL,
                last_checked_at TEXT,
                status TEXT NOT NULL DEFAULT 'candidate'
                    CHECK (status IN ('candidate','active','paused','inaccessible','rejected')),
                research_category TEXT NOT NULL DEFAULT 'general',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                platform_id TEXT,
                UNIQUE (provider, source_type, identity_key)
            );
            CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);
            CREATE INDEX IF NOT EXISTS idx_sources_category ON sources(research_category);
            CREATE INDEX IF NOT EXISTS idx_sources_provider ON sources(provider);
            CREATE TABLE IF NOT EXISTS source_relationships (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                related_source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                relationship_type TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                evidence_reference TEXT NOT NULL,
                UNIQUE (source_id, related_source_id, relationship_type),
                CHECK (source_id != related_source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_relationships_related
                ON source_relationships(related_source_id);
        """)


def _connect(path: Path) -> sqlite3.Connection:
    initialize_database(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _source(row: sqlite3.Row) -> Source:
    return Source(**{field: row[field] for field in Source.__dataclass_fields__})


def add_source(
    path: Path,
    *,
    provider: str,
    source_type: str,
    identifier: str,
    canonical_url: str | None = None,
    display_name: str | None = None,
    description: str | None = None,
    discovery_method: str = "manually_added",
    discovered_from: str | None = None,
    status: str = "candidate",
    research_category: str = "general",
    notes: str | None = None,
    platform_id: str | int | None = None,
) -> tuple[Source, bool]:
    """Insert a source without replacing metadata on duplicate identity."""
    provider = _slug(provider, "provider")
    source_type = _slug(source_type, "source type")
    discovery_method = _slug(discovery_method, "discovery method")
    research_category = _slug(research_category, "research category")
    status = status.strip().casefold()
    if status not in STATUSES:
        raise RegistryError(f"Invalid status '{status}'. Choose: {', '.join(sorted(STATUSES))}.")
    normalized_identifier, derived_url, identity_key = _normalize_identity(provider, identifier)
    if canonical_url is not None:
        canonical_url = _canonical_web_url(canonical_url)
    elif derived_url:
        canonical_url = derived_url
    now = _now()
    db = _connect(path)
    try:
        cursor = db.execute("""
            INSERT OR IGNORE INTO sources
            (provider, source_type, identifier, identity_key, canonical_url, display_name,
             description, discovery_method, discovered_from, discovered_at, status,
             research_category, notes, created_at, updated_at, platform_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            provider, source_type, normalized_identifier, identity_key, canonical_url,
            display_name, description, discovery_method, discovered_from, now, status,
            research_category, notes, now, now,
            str(platform_id).strip() if platform_id is not None else None,
        ))
        created = cursor.rowcount == 1
        row = db.execute("""
            SELECT * FROM sources WHERE provider=? AND source_type=? AND identity_key=?
        """, (provider, source_type, identity_key)).fetchone()
        db.commit()
        if row is None:
            raise RegistryError("Could not save source.")
        return _source(row), created
    except sqlite3.IntegrityError as error:
        db.rollback()
        raise RegistryError(f"Source could not be saved: {error}.") from None
    finally:
        db.close()


def list_sources(path: Path, *, status: str | None = None, provider: str | None = None,
                 research_category: str | None = None) -> list[Source]:
    clauses: list[str] = []
    values: list[str] = []
    for column, value in (("status", status), ("provider", provider), ("research_category", research_category)):
        if value is not None:
            clauses.append(f"{column} = ?")
            values.append(value.strip().casefold())
    query = "SELECT * FROM sources" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY id"
    db = _connect(path)
    try:
        return [_source(row) for row in db.execute(query, values).fetchall()]
    finally:
        db.close()


def get_source(path: Path, source_id: int) -> Source:
    db = _connect(path)
    try:
        row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise RegistryError(f"No source found with ID {source_id}.")
        return _source(row)
    finally:
        db.close()


def search_sources(path: Path, query: str) -> list[Source]:
    query = query.strip()
    if not query:
        raise RegistryError("Search query cannot be empty.")
    escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped_query}%"
    db = _connect(path)
    try:
        rows = db.execute("""
            SELECT * FROM sources
            WHERE identifier LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR canonical_url LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR display_name LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR description LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR research_category LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR notes LIKE ? ESCAPE '\\' COLLATE NOCASE
            ORDER BY id
        """, (pattern,) * 6).fetchall()
        return [_source(row) for row in rows]
    finally:
        db.close()


def save_search_candidates(path: Path, results: list[object]) -> dict[str, object]:
    """Additively record public chat candidates and literal search provenance."""
    if not results:
        return {"created": 0, "ids": {}}
    initialize_database(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    created = 0
    ids: dict[tuple[str, str], int] = {}
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS search_discoveries (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            search_query TEXT NOT NULL,
            discovery_method TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            UNIQUE(source_id, search_query, discovery_method)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_search_discoveries_source ON search_discoveries(source_id)")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (3, ?)", (_now(),))
        for result in results:
            kind = str(getattr(result, "entity_type"))
            username = str(getattr(result, "username") or "").casefold()
            if kind not in {"channel", "group"} or not re.fullmatch(r"[a-z0-9_]{5,32}", username):
                continue
            numeric = str(getattr(result, "telegram_id") or "")
            identifier, url, key = normalize_telegram_identifier("@" + username)
            by_name = db.execute("SELECT * FROM sources WHERE provider='telegram' AND identity_key=?",
                                 (key,)).fetchall()
            if any(row["platform_id"] and numeric and row["platform_id"] != numeric for row in by_name):
                raise RegistryError("Identity conflict: a search username belongs to a different Telegram ID.")
            if any(row["source_type"] != kind for row in by_name):
                raise RegistryError("Identity conflict: a search username has a different registered entity type.")
            by_id = db.execute("SELECT * FROM sources WHERE provider='telegram' AND platform_id=?",
                               (numeric,)).fetchall() if numeric else []
            if any(row["source_type"] != kind for row in by_id):
                raise RegistryError("Identity conflict: a Telegram ID has a different registered entity type.")
            matches = [row for row in by_id + by_name if row["source_type"] == kind]
            if matches:
                source_id = int(matches[0]["id"])
            else:
                timestamp = str(getattr(result, "discovered_at"))
                now = _now()
                cursor = db.execute("""INSERT INTO sources
                    (provider, source_type, identifier, identity_key, canonical_url, display_name,
                     description, discovery_method, discovered_from, discovered_at, status,
                     research_category, notes, created_at, updated_at, platform_id)
                    VALUES ('telegram', ?, ?, ?, ?, ?, ?, 'telegram_public_search', ?, ?,
                            'candidate', 'general', NULL, ?, ?, ?)""",
                    (kind, identifier, key, url, getattr(result, "display_name"),
                     getattr(result, "description"), str(getattr(result, "search_query")),
                     timestamp, now, now, numeric or None))
                source_id = int(cursor.lastrowid)
                created += 1
            db.execute("""INSERT OR IGNORE INTO search_discoveries
                (source_id, search_query, discovery_method, discovered_at) VALUES (?, ?, ?, ?)""",
                (source_id, str(getattr(result, "search_query")),
                 str(getattr(result, "discovery_method")), str(getattr(result, "discovered_at"))))
            ids[(kind, username)] = source_id
        db.commit()
        return {"created": created, "ids": ids}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_source(path: Path, source_id: int, **changes: str | None) -> Source:
    allowed = {"canonical_url", "display_name", "description", "discovery_method",
               "discovered_from", "last_checked_at", "status", "research_category", "notes", "platform_id"}
    if not changes or set(changes) - allowed:
        extra = sorted(set(changes) - allowed)
        raise RegistryError(f"No supported fields to update.{(' Unsupported: ' + ', '.join(extra)) if extra else ''}")
    if "status" in changes and changes["status"] is not None:
        status = changes["status"].strip().casefold()
        if status not in STATUSES:
            raise RegistryError(f"Invalid status '{status}'. Choose: {', '.join(sorted(STATUSES))}.")
        changes["status"] = status
    if "research_category" in changes and changes["research_category"] is not None:
        changes["research_category"] = _slug(changes["research_category"], "research category")
    if "discovery_method" in changes and changes["discovery_method"] is not None:
        changes["discovery_method"] = _slug(changes["discovery_method"], "discovery method")
    if "canonical_url" in changes and changes["canonical_url"] is not None:
        changes["canonical_url"] = _canonical_web_url(changes["canonical_url"])
    if "platform_id" in changes and changes["platform_id"] is not None:
        changes["platform_id"] = str(changes["platform_id"]).strip()
    changes["updated_at"] = _now()
    assignments = ", ".join(f"{column}=?" for column in changes)
    db = _connect(path)
    try:
        cursor = db.execute(f"UPDATE sources SET {assignments} WHERE id=?", (*changes.values(), source_id))
        if cursor.rowcount == 0:
            raise RegistryError(f"No source found with ID {source_id}.")
        db.commit()
        row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        assert row is not None
        return _source(row)
    finally:
        db.close()


def pause_source(path: Path, source_id: int) -> Source:
    return update_source(path, source_id, status="paused")


def resume_source(path: Path, source_id: int) -> Source:
    return update_source(path, source_id, status="active")


def add_relationship(path: Path, *, source_id: int, related_source_id: int,
                     relationship_type: str, evidence_reference: str) -> tuple[SourceRelationship, bool]:
    relationship_type = _slug(relationship_type, "relationship type")
    evidence_reference = evidence_reference.strip()
    if not evidence_reference:
        raise RegistryError("An evidence reference is required for a relationship.")
    db = _connect(path)
    try:
        cursor = db.execute("""
            INSERT OR IGNORE INTO source_relationships
            (source_id, related_source_id, relationship_type, discovered_at, evidence_reference)
            VALUES (?, ?, ?, ?, ?)
        """, (source_id, related_source_id, relationship_type, _now(), evidence_reference))
        created = cursor.rowcount == 1
        row = db.execute("""
            SELECT * FROM source_relationships
            WHERE source_id=? AND related_source_id=? AND relationship_type=?
        """, (source_id, related_source_id, relationship_type)).fetchone()
        db.commit()
        if row is None:
            raise RegistryError("Relationship could not be saved; check both source IDs.")
        return SourceRelationship(**dict(row)), created
    except sqlite3.IntegrityError:
        db.rollback()
        raise RegistryError("Relationship could not be saved; check both source IDs and ensure they differ.") from None
    finally:
        db.close()


def source_stats(path: Path) -> dict[str, object]:
    db = _connect(path)
    try:
        total = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        statuses = {row[0]: row[1] for row in db.execute("SELECT status, COUNT(*) FROM sources GROUP BY status")}
        providers = {row[0]: row[1] for row in db.execute("SELECT provider, COUNT(*) FROM sources GROUP BY provider")}
        categories = {row[0]: row[1] for row in db.execute("SELECT research_category, COUNT(*) FROM sources GROUP BY research_category")}
        relationships = db.execute("SELECT COUNT(*) FROM source_relationships").fetchone()[0]
        return {"sources": total, "by_status": statuses, "by_provider": providers,
                "by_category": categories, "relationships": relationships}
    finally:
        db.close()


def to_json(value: object) -> str:
    """Serialize registry records and collections as readable JSON."""
    if isinstance(value, Source) or isinstance(value, SourceRelationship):
        value = asdict(value)
    elif isinstance(value, list):
        value = [asdict(item) if isinstance(item, (Source, SourceRelationship)) else item for item in value]
    return json.dumps(value, indent=2, ensure_ascii=False)


def save_inspection_record(
    path: Path,
    *,
    identifier: str,
    source_type: str,
    metadata: dict[str, object],
    inspected_at: str,
    limit: int,
    findings: dict[str, object],
    warnings: list[str],
) -> int:
    """Additively migrate and atomically save non-content inspection metadata."""
    provider = "telegram"
    source_type = _slug(source_type, "source type")
    normalized_identifier, canonical_url, identity_key = _normalize_identity(provider, identifier)
    platform_id = str(metadata.get("telegram_id") or "").strip()
    if not platform_id:
        raise RegistryError("Telegram did not provide a stable numeric ID; inspection was not saved.")
    display_name = str(metadata.get("title") or "").strip() or None
    description = str(metadata.get("description") or "").strip() or None
    if identifier.startswith("telegram-id:"):
        canonical_url = None

    initialize_database(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        db.execute("BEGIN IMMEDIATE")
        # Migration 1 is additive: record versions and create inspection summaries.
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )""")
        applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
        if 1 not in applied:
            db.execute("""CREATE TABLE IF NOT EXISTS inspection_runs (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                inspected_at TEXT NOT NULL,
                message_limit INTEGER NOT NULL,
                messages_inspected INTEGER NOT NULL,
                messages_containing_urls INTEGER NOT NULL,
                messages_containing_telegram_references INTEGER NOT NULL,
                messages_containing_external_urls INTEGER NOT NULL,
                messages_containing_vless_candidates INTEGER NOT NULL,
                messages_containing_shadowsocks_candidates INTEGER NOT NULL,
                earliest_message_date TEXT,
                latest_message_date TEXT,
                warnings_json TEXT NOT NULL,
                completion_status TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_inspection_runs_source_time "
                       "ON inspection_runs(source_id, inspected_at)")
            db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)", (_now(),))

        identity_rows = db.execute(
            "SELECT id, source_type, platform_id FROM sources WHERE provider=? AND identity_key=?",
            (provider, identity_key),
        ).fetchall()
        if any(row["platform_id"] and row["platform_id"] != platform_id for row in identity_rows):
            raise RegistryError(
                "Identity conflict: this Telegram username is already associated with a different numeric ID."
            )
        existing = next((row for row in identity_rows if row["source_type"] == source_type), None)
        if identifier.startswith("telegram-id:") and existing is None:
            existing = db.execute(
                "SELECT id, source_type, platform_id FROM sources "
                "WHERE provider=? AND source_type=? AND platform_id=? ORDER BY id LIMIT 1",
                (provider, source_type, platform_id),
            ).fetchone()
        if existing is None:
            now = _now()
            cursor = db.execute("""INSERT INTO sources
                (provider, source_type, identifier, identity_key, canonical_url, display_name,
                 description, discovery_method, discovered_from, discovered_at, last_checked_at,
                 status, research_category, notes, created_at, updated_at, platform_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'manually_added', NULL, ?, ?, 'candidate',
                        'general', NULL, ?, ?, ?)""",
                (provider, source_type, normalized_identifier, identity_key, canonical_url,
                 display_name, description, inspected_at, inspected_at, now, now, platform_id))
            source_id = int(cursor.lastrowid)
        else:
            source_id = int(existing["id"])
            db.execute("""UPDATE sources SET
                canonical_url=COALESCE(NULLIF(canonical_url,''), ?),
                display_name=COALESCE(NULLIF(display_name,''), ?),
                description=COALESCE(NULLIF(description,''), ?),
                platform_id=COALESCE(NULLIF(platform_id,''), ?),
                last_checked_at=?, updated_at=?
                WHERE id=?""",
                (canonical_url, display_name, description, platform_id, inspected_at, _now(), source_id))

        db.execute("""INSERT INTO inspection_runs
            (source_id, inspected_at, message_limit, messages_inspected,
             messages_containing_urls, messages_containing_telegram_references,
             messages_containing_external_urls, messages_containing_vless_candidates,
             messages_containing_shadowsocks_candidates, earliest_message_date,
             latest_message_date, warnings_json, completion_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed')""",
            (source_id, inspected_at, limit,
             int(findings["messages_inspected"]),
             int(findings["messages_containing_urls"]),
             int(findings["messages_containing_telegram_references"]),
             int(findings["messages_containing_external_urls"]),
             int(findings["messages_containing_vless_candidates"]),
             int(findings["messages_containing_shadowsocks_candidates"]),
             findings.get("earliest_inspected_message_date"),
             findings.get("latest_inspected_message_date"),
             json.dumps(warnings, ensure_ascii=False)))
        db.commit()
        return source_id
    except sqlite3.IntegrityError as error:
        db.rollback()
        raise RegistryError(f"Inspection summary could not be saved: {error}.") from None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def save_reference_discoveries(
    path: Path,
    *,
    origin_source_id: str,
    origin_identifier: str,
    origin_source_type: str,
    origin_title: str | None,
    discoveries: list[dict[str, object]],
) -> dict[str, int]:
    """Persist candidate source records and per-message reference provenance.

    All writes, including additive schema migrations, share one transaction.
    Reference candidates remain candidates and source metadata is never replaced.
    """
    provider = "telegram"
    origin_source_type = _slug(origin_source_type, "source type")
    origin_identifier = origin_identifier.strip()
    normalized_origin, origin_url, origin_key = _normalize_identity(provider, origin_identifier)
    origin_numeric_id = str(origin_source_id).strip()
    if not origin_numeric_id.isdigit():
        raise RegistryError("Origin Telegram source ID must be numeric.")

    initialize_database(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        db.execute("BEGIN IMMEDIATE")
        # Migration 1: Task 4 inspection summaries. Migration 2: reference provenance.
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )""")
        applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
        if 1 not in applied:
            db.execute("""CREATE TABLE IF NOT EXISTS inspection_runs (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                inspected_at TEXT NOT NULL,
                message_limit INTEGER NOT NULL,
                messages_inspected INTEGER NOT NULL,
                messages_containing_urls INTEGER NOT NULL,
                messages_containing_telegram_references INTEGER NOT NULL,
                messages_containing_external_urls INTEGER NOT NULL,
                messages_containing_vless_candidates INTEGER NOT NULL,
                messages_containing_shadowsocks_candidates INTEGER NOT NULL,
                earliest_message_date TEXT,
                latest_message_date TEXT,
                warnings_json TEXT NOT NULL,
                completion_status TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_inspection_runs_source_time "
                       "ON inspection_runs(source_id, inspected_at)")
            db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)", (_now(),))
        if 2 not in applied:
            db.execute("""CREATE TABLE IF NOT EXISTS source_references (
                id INTEGER PRIMARY KEY,
                origin_source_id TEXT NOT NULL,
                origin_source_registry_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                origin_message_id INTEGER NOT NULL,
                discovered_at TEXT NOT NULL,
                reference_type TEXT NOT NULL,
                normalized_target TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0),
                candidate_source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
                evidence_reference TEXT NOT NULL,
                UNIQUE (origin_source_id, origin_message_id, reference_type, normalized_target)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_source_references_target "
                       "ON source_references(reference_type, normalized_target)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_source_references_origin "
                       "ON source_references(origin_source_id, origin_message_id)")
            db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)", (_now(),))

        origin_matches = db.execute(
            "SELECT id, source_type, platform_id FROM sources WHERE provider=? AND identity_key=?",
            (provider, origin_key),
        ).fetchall()
        if any(row["platform_id"] and row["platform_id"] != origin_numeric_id for row in origin_matches):
            raise RegistryError("Origin identity conflict: this Telegram username has a different numeric ID.")
        origin_row = next((row for row in origin_matches if row["source_type"] == origin_source_type), None)
        if origin_row is None:
            now = _now()
            cursor = db.execute("""INSERT INTO sources
                (provider, source_type, identifier, identity_key, canonical_url, display_name,
                 description, discovery_method, discovered_from, discovered_at, last_checked_at,
                 status, research_category, notes, created_at, updated_at, platform_id)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 'manually_added', NULL, ?, NULL,
                        'candidate', 'general', NULL, ?, ?, ?)""",
                (provider, origin_source_type, normalized_origin, origin_key, origin_url,
                 origin_title, _now(), now, now, origin_numeric_id))
            origin_registry_id = int(cursor.lastrowid)
        else:
            origin_registry_id = int(origin_row["id"])
            db.execute("""UPDATE sources SET
                canonical_url=COALESCE(NULLIF(canonical_url,''), ?),
                display_name=COALESCE(NULLIF(display_name,''), ?),
                platform_id=COALESCE(NULLIF(platform_id,''), ?), updated_at=?
                WHERE id=?""",
                (origin_url, origin_title, origin_numeric_id, _now(), origin_registry_id))

        candidate_created = 0
        for discovery in discoveries:
            ref_type = _slug(str(discovery["reference_type"]), "reference type")
            candidate_provider = _slug(str(discovery["candidate_provider"]), "provider")
            candidate_type = _slug(str(discovery["candidate_source_type"]), "source type")
            candidate_identifier = str(discovery["candidate_identifier"])
            normalized, canonical_url, identity = _normalize_identity(candidate_provider, candidate_identifier)
            message_id = int(discovery["origin_message_id"])
            timestamp = str(discovery["discovered_at"])
            target = str(discovery["normalized_target"])
            occurrences = int(discovery["occurrence_count"])
            if message_id <= 0 or occurrences <= 0:
                raise RegistryError("Reference message IDs and occurrence counts must be positive.")
            first_seen = f"telegram:{origin_numeric_id}/{message_id}"
            now = _now()
            cursor = db.execute("""INSERT OR IGNORE INTO sources
                (provider, source_type, identifier, identity_key, canonical_url, display_name,
                 description, discovery_method, discovered_from, discovered_at, last_checked_at,
                 status, research_category, notes, created_at, updated_at, platform_id)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, 'referenced_by_message', ?, ?, NULL,
                        'candidate', 'general', NULL, ?, ?, NULL)""",
                (candidate_provider, candidate_type, normalized, identity, canonical_url,
                 first_seen, timestamp, now, now))
            candidate_created += max(cursor.rowcount, 0)
            candidate_row = db.execute("""SELECT id FROM sources
                WHERE provider=? AND source_type=? AND identity_key=?""",
                (candidate_provider, candidate_type, identity)).fetchone()
            if candidate_row is None:
                raise RegistryError("Could not create or resolve a reference candidate.")
            candidate_id = int(candidate_row["id"])
            evidence = f"telegram:{origin_numeric_id}/{message_id}"
            db.execute("""INSERT INTO source_references
                (origin_source_id, origin_source_registry_id, origin_message_id, discovered_at,
                 reference_type, normalized_target, occurrence_count, candidate_source_id,
                 evidence_reference)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(origin_source_id, origin_message_id, reference_type, normalized_target)
                DO UPDATE SET occurrence_count=excluded.occurrence_count,
                              candidate_source_id=COALESCE(source_references.candidate_source_id,
                                                           excluded.candidate_source_id)""",
                (origin_numeric_id, origin_registry_id, message_id, timestamp, ref_type,
                 target, occurrences, candidate_id, evidence))
        db.commit()
        return {
            "origin_source_registry_id": origin_registry_id,
            "candidate_sources_created": candidate_created,
            "provenance_records_saved": len(discoveries),
        }
    except sqlite3.IntegrityError as error:
        db.rollback()
        raise RegistryError(f"Reference records could not be saved: {error}.") from None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
