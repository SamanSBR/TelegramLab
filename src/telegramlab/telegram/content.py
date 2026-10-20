"""Bounded search of public channel content and an optional local SQLite index."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from telethon import functions, types

from telegramlab.core.config import PROJECT_ROOT, Settings
from telegramlab.telegram.inspector import (
    InspectionError, InvalidSourceError, _raise_mapped_error, normalize_target,
)

KINDS = {"message", "document", "photo", "video", "audio", "all"}
_FILTERS = {
    "message": types.InputMessagesFilterEmpty,
    "document": types.InputMessagesFilterDocument,
    "photo": types.InputMessagesFilterPhotos,
    "video": types.InputMessagesFilterVideo,
    # Empty includes both music and voice notes; there is no combined server filter.
    "audio": types.InputMessagesFilterEmpty,
    "all": types.InputMessagesFilterEmpty,
}


class ContentSearchError(InspectionError):
    """A content search could not be completed."""


@dataclass(slots=True)
class ContentHit:
    source_id: str
    source_channel: str
    message_id: int
    message_date: str | None
    message_url: str | None
    search_query: str
    discovered_at: str
    content_type: str
    match_type: str
    text: str
    filename: str | None
    media_metadata: str | None


@dataclass(slots=True)
class ContentResponse:
    query: str
    content_type: str
    source: str | None
    mode: str
    limit: int
    results: list[ContentHit]
    scanned: int
    saved: int
    warnings: list[str]
    completion_status: str = "completed"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(value: str) -> str:
    """Normalize Arabic/Persian variants for matching, without changing evidence."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه"}))
    return "".join(char for char in value if char not in {"\u0640", "\u200c", "\u200d"} and
                   unicodedata.category(char) != "Mn")


def validate(query: str, kind: str, limit: int, source: str | None) -> tuple[str, str | None]:
    query = query.strip()
    if not query or len(query) > 256 or any(ord(c) < 32 for c in query):
        raise ContentSearchError("Search query must be 1–256 printable characters.")
    if kind not in KINDS or not 1 <= limit <= 100:
        raise ContentSearchError("Content type must be supported and limit must be between 1 and 100.")
    if source is not None:
        source = normalize_target(source)
        if source.startswith("-"):
            raise InvalidSourceError("Content search requires a public channel username.")
    return query, source


def _media(message: Any) -> tuple[str, str | None, str | None]:
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaPhoto):
        return "photo", None, "photo"
    if isinstance(media, types.MessageMediaDocument):
        document = getattr(media, "document", None)
        mime = getattr(document, "mime_type", None) or ""
        attributes = getattr(document, "attributes", None) or []
        filename = next((a.file_name for a in attributes if isinstance(a, types.DocumentAttributeFilename)), None)
        audio = next((a for a in attributes if isinstance(a, types.DocumentAttributeAudio)), None)
        if audio or mime.startswith("audio/"):
            kind = "audio"
        elif mime.startswith("video/") or any(isinstance(a, types.DocumentAttributeVideo) for a in attributes):
            kind = "video"
        else:
            kind = "document"
        return kind, filename, mime or None
    return "message", None, None


def _match(query: str, message_text: str, filename: str | None, metadata: str | None) -> str:
    term = normalize_text(query)
    body = normalize_text(message_text)
    if term in body:
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", body):
            return "exact_text"
        return "partial_text"
    if filename and term in normalize_text(filename):
        return "filename"
    return "metadata"


def _hit(message: Any, entity: Any, query: str, now: str) -> ContentHit | None:
    mid = getattr(message, "id", None)
    sid = getattr(entity, "id", None)
    username = getattr(entity, "username", None)
    if not isinstance(mid, int) or sid is None or not username:
        return None
    date = getattr(message, "date", None)
    if date is not None and date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    kind, filename, metadata = _media(message)
    body = getattr(message, "raw_text", None) or getattr(message, "message", None) or ""
    return ContentHit(str(sid), "@" + username.casefold(), mid,
                      date.isoformat() if date else None,
                      f"https://t.me/{username}/{mid}", query, now, kind,
                      _match(query, body, filename, metadata), body, filename, metadata)


def _public_channel(entity: Any) -> bool:
    return isinstance(entity, types.Channel) and bool(getattr(entity, "broadcast", False)) and bool(getattr(entity, "username", None))


def _safe_index_path(path: Path) -> Path:
    path = Path(path).expanduser().resolve()
    settings = Settings.from_env()
    forbidden = {settings.database_path.resolve(), settings.sources_database_path.resolve(),
                 (PROJECT_ROOT / "telegram_lab.db").resolve(), (PROJECT_ROOT / "data/sources.db").resolve(),
                 (PROJECT_ROOT / "telegram_lab.session").resolve()}
    if path in forbidden or path.suffix.casefold() == ".session":
        raise ContentSearchError("Content index must use a separate SQLite file.")
    return path


def save_hits(path: Path, hits: list[ContentHit]) -> int:
    if not hits:
        return 0
    path = _safe_index_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS content_hits (
            source_id TEXT NOT NULL, message_id INTEGER NOT NULL, source_channel TEXT NOT NULL,
            message_date TEXT, message_url TEXT, search_query TEXT NOT NULL,
            discovered_at TEXT NOT NULL, content_type TEXT NOT NULL, match_type TEXT NOT NULL,
            text TEXT NOT NULL, filename TEXT, media_metadata TEXT,
            PRIMARY KEY (source_id, message_id))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_content_type ON content_hits(content_type)")
        db.execute("""CREATE TABLE IF NOT EXISTS content_discoveries (
            source_id TEXT NOT NULL, message_id INTEGER NOT NULL, search_query TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            PRIMARY KEY (source_id, message_id, search_query),
            FOREIGN KEY (source_id, message_id) REFERENCES content_hits(source_id, message_id))""")
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1, ?)", (datetime.now(UTC).isoformat(timespec="seconds"),))
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (2, ?)", (datetime.now(UTC).isoformat(timespec="seconds"),))
        before = db.total_changes
        db.executemany("""INSERT OR IGNORE INTO content_hits VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                       [(h.source_id, h.message_id, h.source_channel, h.message_date, h.message_url,
                         h.search_query, h.discovered_at, h.content_type, h.match_type, h.text,
                         h.filename, h.media_metadata) for h in hits])
        created = db.total_changes - before
        db.executemany("INSERT OR IGNORE INTO content_discoveries VALUES (?,?,?,?)",
                       [(h.source_id, h.message_id, h.search_query, h.discovered_at) for h in hits])
        return created


def local_search(path: Path, query: str, kind: str, limit: int, source: str | None, now: str) -> list[ContentHit]:
    path = _safe_index_path(path)
    if not path.is_file():
        return []
    # Open read-only: --local must never create or migrate a database.
    found = []
    term = normalize_text(query)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_hits'").fetchone():
            return []
        for row in db.execute("SELECT * FROM content_hits ORDER BY discovered_at DESC, source_id, message_id"):
            if kind != "all" and row["content_type"] != kind:
                continue
            if source and row["source_channel"].casefold() != "@" + source:
                continue
            if not any(term in normalize_text(row[key] or "") for key in ("text", "filename", "media_metadata")):
                continue
            values = dict(row)
            values["search_query"] = query
            values["discovered_at"] = now
            values["match_type"] = _match(query, values["text"], values["filename"], values["media_metadata"])
            found.append(ContentHit(**values))
            if len(found) >= limit:
                break
    return found


async def search_content(client: Any | None, query: str, *, kind: str = "all", source: str | None = None,
                         limit: int = 20, local: bool = False, save: bool = False,
                         index_path: Path, progress: Callable[[str], None] | None = None) -> ContentResponse:
    query, source = validate(query, kind, limit, source)
    report = progress or (lambda _: None)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if local:
        report("[1/2] Searching local content index")
        try:
            hits = local_search(index_path, query, kind, limit, source, now)
        except sqlite3.DatabaseError as error:
            raise ContentSearchError(f"Local content index could not be read ({type(error).__name__}).") from None
        report(f"[2/2] Found {len(hits)} local results")
        return ContentResponse(query, kind, "@" + source if source else None, "local", limit,
                               hits, len(hits), 0, ["Local results cover only previously saved public content."])
    if client is None:
        raise ContentSearchError("Telegram content search requires an existing authorized session.")
    if save:
        _safe_index_path(index_path)
    report("[1/4] Search query and scope validated")
    try:
        entity = await client.get_entity(source) if source else None
        if source and not _public_channel(entity):
            raise InvalidSourceError("Source must be a public broadcast channel with a username.")
        report("[2/4] Public channel resolved" if source else "[2/4] Global search scope prepared")
        filter_value = _FILTERS[kind]()
        if source:
            response = await client(functions.messages.SearchRequest(
                peer=entity, q=query, filter=filter_value, min_date=None, max_date=None,
                offset_id=0, add_offset=0, limit=limit, max_id=0, min_id=0, hash=0))
            entities = {entity.id: entity}
        else:
            response = await client(functions.messages.SearchGlobalRequest(
                q=query, filter=filter_value, min_date=None, max_date=None, offset_rate=0,
                offset_peer=types.InputPeerEmpty(), offset_id=0, limit=limit))
            entities = {item.id: item for item in getattr(response, "chats", []) if _public_channel(item)}
    except Exception as error:
        _raise_mapped_error(error)
        if isinstance(error, InspectionError):
            raise
        raise ContentSearchError(f"Telegram content search failed ({type(error).__name__}).") from None
    hits: list[ContentHit] = []
    seen: set[tuple[str, int]] = set()
    messages = getattr(response, "messages", [])
    for message in messages:
        peer = getattr(message, "peer_id", None)
        channel = entities.get(getattr(peer, "channel_id", None))
        if channel is None:
            continue
        hit = _hit(message, channel, query, now)
        if hit is None or (kind != "all" and hit.content_type != kind):
            continue
        key = (hit.source_id, hit.message_id)
        if key not in seen:
            seen.add(key)
            hits.append(hit)
    report(f"[3/4] Processed {len(messages)} returned messages; {len(hits)} public channel results")
    try:
        saved = save_hits(index_path, hits) if save else 0
    except sqlite3.DatabaseError as error:
        raise ContentSearchError(f"Content index could not be saved ({type(error).__name__}).") from None
    report(f"[4/4] Saved {saved} new results" if save else "[4/4] Search complete")
    return ContentResponse(query, kind, "@" + source if source else None, "telegram", limit,
                           hits, len(messages), saved,
                           ["Telegram search is ranked and incomplete; results reflect only accessible public channels.",
                            "Real names in content are mentions, not verified identities."])
