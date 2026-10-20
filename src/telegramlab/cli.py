"""Command-line interface for offline TelegramLab operations."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime

from telegramlab import __version__
from telegramlab.core.config import Settings
from telegramlab.sources.registry import (
    RegistryError,
    Source,
    add_source,
    get_source,
    list_sources,
    pause_source,
    resume_source,
    search_sources,
    source_stats,
    to_json,
    update_source,
)
from telegramlab.storage.database import database_exists
from telegramlab.telegram.client import (
    MissingSessionError,
    create_client,
    require_existing_session,
)
from telegramlab.telegram.inspector import (
    FloodWaitInspectionError,
    InspectionError,
    InvalidCredentialsError,
    NetworkInspectionError,
    inspect_source,
    normalize_target,
)
from telegramlab.telegram.explorer import ExplorationResult, explore_source
from telegramlab.telegram.search import SearchError, search_public, validate_search_query
from telegramlab.telegram.content import ContentSearchError, search_content, validate as validate_content
from telegramlab.research.workflow import ResearchError, run_research

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?hash|api[_-]?id|token|password|secret)\s*([=:])\s*([^\s,;]+)"),
    re.compile(r"\b\d{6,}:\w{20,}\b"),
)


class SecretRedactionFilter(logging.Filter):
    """Redact common credential assignments and bot-token-shaped strings."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for index, pattern in enumerate(_SECRET_PATTERNS):
            message = pattern.sub(r"\1\2[REDACTED]" if index == 0 else "[REDACTED]", message)
        record.msg = message
        record.args = ()
        return True


def configure_logging() -> None:
    """Configure concise key-value logging with credential redaction."""
    handler = logging.StreamHandler()
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s level=%(levelname)s logger=%(name)s message=%(message)s"
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")


def _inspection_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("limit must be an integer between 1 and 100") from None
    if not 1 <= limit <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return limit


def _research_source_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("max-sources must be an integer between 1 and 10") from None
    if not 1 <= limit <= 10:
        raise argparse.ArgumentTypeError("max-sources must be between 1 and 10")
    return limit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegramlab", description="Local-first Telegram research utility")
    parser.add_argument("--version", action="version", version=f"telegramlab {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status", help="Show offline application and local file status")

    inspect = commands.add_parser("inspect", help="Read-only inspection of one accessible Telegram channel or group")
    inspect.add_argument("target", help="Public username or an already accessible marked dialog ID")
    inspect.add_argument("--limit", type=_inspection_limit, default=30, help="Recent messages to inspect (1–100, default 30)")
    inspect.add_argument("--save", action="store_true", help="Save metadata and aggregate summary in the source registry")
    inspect.add_argument("--json", action="store_true", help="Print structured JSON")

    explore = commands.add_parser("explore", help="Explore references in one accessible Telegram source")
    explore.add_argument("target", help="Public username or an already accessible marked dialog ID")
    explore.add_argument("--limit", type=_inspection_limit, default=30,
                         help="Recent messages to inspect (1–100, default 30)")
    explore.add_argument("--save", action="store_true", help="Save candidates and reference provenance")
    explore.add_argument("--json", action="store_true", help="Print structured JSON")

    discovery = commands.add_parser("search", help="Discover public Telegram chats or search the local registry")
    discovery.add_argument("query", help="Exact @username or literal name, partial name, or keyword query")
    discovery.add_argument("--mode", choices=("telegram", "local", "both"), default="telegram",
                           help="Search Telegram, the local registry, or both (default: telegram)")
    discovery.add_argument("--local", action="store_true", help="Alias for --mode local; makes no Telegram request")
    discovery.add_argument("--type", dest="entity_type", choices=("channel", "group", "all"), default="all",
                           help="Filter channel or group results (default: all)")
    discovery.add_argument("--limit", type=_inspection_limit, default=20,
                           help="Maximum results and Telegram request limit (1–100, default 20)")
    discovery.add_argument("--json", action="store_true", help="Print structured JSON; progress goes to stderr")
    discovery.add_argument("--save", action="store_true", help="Save eligible public chats as inactive candidates with query provenance")
    discovery.add_argument("--verbose", action="store_true", help="Show discovery methods and warnings per result")

    content = commands.add_parser("content", help="Search accessible public Telegram content")
    content_actions = content.add_subparsers(dest="content_command", required=True)
    content_search = content_actions.add_parser("search", help="Search public messages or the local content index")
    content_search.add_argument("query")
    content_search.add_argument("--source", help="Limit to one public broadcast channel username")
    content_search.add_argument("--type", dest="content_type", choices=("message", "document", "photo", "video", "audio", "all"), default="all")
    content_search.add_argument("--limit", type=_inspection_limit, default=20)
    content_search.add_argument("--local", action="store_true", help="Search the saved index without Telegram access")
    content_search.add_argument("--save", action="store_true", help="Index returned public content locally")
    content_search.add_argument("--json", action="store_true")

    research = commands.add_parser("research", help="Run a bounded source and evidence workflow")
    research.add_argument("query", help="Literal source and optional content search query")
    research.add_argument("--limit", type=_inspection_limit, default=20,
                          help="Source result and per-source message limit (1–100)")
    research.add_argument("--max-sources", type=_research_source_limit, default=3,
                          help="Maximum discovered sources to inspect or explore (1–10, default 3)")
    research.add_argument("--inspect", action="store_true")
    research.add_argument("--explore", action="store_true")
    research.add_argument("--content", action="store_true")
    research.add_argument("--local", action="store_true", help="Review saved source and content evidence offline")
    research.add_argument("--save", action="store_true", help="Persist evidence and workflow history")
    research.add_argument("--json", action="store_true")

    source = commands.add_parser("source", help="Manage the local research source registry")
    actions = source.add_subparsers(dest="source_command", required=True)

    add = actions.add_parser("add", help="Add a source as a candidate")
    add.add_argument("--provider", required=True)
    add.add_argument("--type", dest="source_type", required=True)
    add.add_argument("--identifier", required=True)
    add.add_argument("--url", dest="canonical_url")
    add.add_argument("--name", dest="display_name")
    add.add_argument("--description")
    add.add_argument("--discovery-method", default="manually_added")
    add.add_argument("--discovered-from")
    add.add_argument("--category", dest="research_category", default="general")
    add.add_argument("--notes")
    add.add_argument("--platform-id", help="Stable provider ID, only when legitimately known")
    _add_json_option(add)

    listing = actions.add_parser("list", help="List registered sources")
    listing.add_argument("--status", choices=("candidate", "active", "paused", "inaccessible", "rejected"))
    listing.add_argument("--provider")
    listing.add_argument("--category", dest="research_category")
    _add_json_option(listing)

    show = actions.add_parser("show", help="Show one source")
    show.add_argument("id", type=int)
    _add_json_option(show)

    search = actions.add_parser("search", help="Search source metadata")
    search.add_argument("query")
    _add_json_option(search)

    update = actions.add_parser("update", help="Update source metadata")
    update.add_argument("id", type=int)
    update.add_argument("--name", dest="display_name")
    update.add_argument("--description")
    update.add_argument("--url", dest="canonical_url")
    update.add_argument("--category", dest="research_category")
    update.add_argument("--notes")
    update.add_argument("--discovery-method")
    update.add_argument("--discovered-from")
    update.add_argument("--status", choices=("candidate", "active", "paused", "inaccessible", "rejected"))
    update.add_argument("--platform-id")
    _add_json_option(update)

    for action_name, help_text in (("pause", "Pause a source"), ("resume", "Resume a source")):
        action = actions.add_parser(action_name, help=help_text)
        action.add_argument("id", type=int)
        _add_json_option(action)

    stats = actions.add_parser("stats", help="Show registry counts")
    _add_json_option(stats)
    return parser


def _render(value: object, as_json: bool) -> None:
    if as_json:
        print(to_json(value))
    elif isinstance(value, Source):
        for key, item in asdict(value).items():
            print(f"{key.replace('_', ' ').title()}: {item if item is not None else '-'}")
    elif isinstance(value, list):
        if not value:
            print("No sources found.")
            return
        for item in value:
            print(f"{item.id}: {item.display_name or item.identifier} [{item.provider}/{item.source_type}] "
                  f"status={item.status} category={item.research_category}")
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                print(f"{key.title()}:")
                for subkey, count in sorted(item.items()):
                    print(f"  {subkey}: {count}")
            else:
                print(f"{key.replace('_', ' ').title()}: {item}")
    else:
        print(value)


def _run_source_command(args: argparse.Namespace, path) -> int:
    action = args.source_command
    if action == "add":
        record, created = add_source(
            path,
            provider=args.provider,
            source_type=args.source_type,
            identifier=args.identifier,
            canonical_url=args.canonical_url,
            display_name=args.display_name,
            description=args.description,
            discovery_method=args.discovery_method,
            discovered_from=args.discovered_from,
            research_category=args.research_category,
            notes=args.notes,
            platform_id=args.platform_id,
        )
        if not args.json:
            print("Source added." if created else "Source already exists; existing metadata was preserved.")
        _render(record, args.json)
        return 0
    if action == "list":
        _render(list_sources(path, status=args.status, provider=args.provider,
                             research_category=args.research_category), args.json)
    elif action == "show":
        _render(get_source(path, args.id), args.json)
    elif action == "search":
        _render(search_sources(path, args.query), args.json)
    elif action == "update":
        changes = {key: value for key, value in vars(args).items()
                   if key in {"canonical_url", "display_name", "description", "research_category",
                              "notes", "discovery_method", "discovered_from", "status", "platform_id"}
                   and value is not None}
        if not changes:
            raise RegistryError("Provide at least one field to update.")
        _render(update_source(path, args.id, **changes), args.json)
    elif action == "pause":
        _render(pause_source(path, args.id), args.json)
    elif action == "resume":
        _render(resume_source(path, args.id), args.json)
    elif action == "stats":
        _render(source_stats(path), args.json)
    return 0


async def _inspect_with_existing_session(target: str, limit: int, save: bool, registry_path, progress):
    settings = Settings.from_env()
    require_existing_session(settings.session_path)
    if settings.api_id is None or not settings.api_hash:
        raise InvalidCredentialsError("TG_API_ID and TG_API_HASH are required; no login prompt will be opened.")
    try:
        client = create_client(settings)
    except MissingSessionError:
        raise
    except ValueError as error:
        raise InvalidCredentialsError(str(error)) from None
    connected = False
    try:
        await client.connect()
        connected = True
        if not await client.is_user_authorized():
            raise InvalidCredentialsError("The existing Telegram session is not authorized; interactive login is disabled.")
        return await inspect_source(
            client, target, limit=limit, progress=progress,
            registry_path=registry_path, save=save,
        )
    except Exception as error:
        if isinstance(error, (InspectionError, MissingSessionError)):
            raise
        from telegramlab.telegram.inspector import _raise_mapped_error
        _raise_mapped_error(error)
        raise InspectionError(f"Telegram inspection failed ({type(error).__name__}).") from None
    finally:
        if connected:
            try:
                await client.disconnect()
            except Exception:
                # Cleanup failures should not replace a completed inspection result.
                pass


async def _explore_with_existing_session(target: str, limit: int, save: bool, registry_path, progress):
    normalized_target = normalize_target(target)
    settings = Settings.from_env()
    require_existing_session(settings.session_path)
    if settings.api_id is None or not settings.api_hash:
        raise InvalidCredentialsError("TG_API_ID and TG_API_HASH are required; no login prompt will be opened.")
    try:
        client = create_client(settings)
    except MissingSessionError:
        raise
    except ValueError as error:
        raise InvalidCredentialsError(str(error)) from None
    connected = False
    try:
        await client.connect()
        connected = True
        if not await client.is_user_authorized():
            raise InvalidCredentialsError("The existing Telegram session is not authorized; interactive login is disabled.")
        return await explore_source(
            client, normalized_target, limit=limit, progress=progress,
            registry_path=registry_path, save=save,
        )
    except Exception as error:
        if isinstance(error, (InspectionError, MissingSessionError)):
            raise
        from telegramlab.telegram.inspector import _raise_mapped_error
        _raise_mapped_error(error)
        raise InspectionError(f"Reference exploration failed ({type(error).__name__}).") from None
    finally:
        if connected:
            try:
                await client.disconnect()
            except Exception:
                pass


def _run_inspect(args: argparse.Namespace) -> int:
    progress = (lambda message: print(message, file=sys.stderr)) if args.json else print
    try:
        result = asyncio.run(_inspect_with_existing_session(
            args.target, args.limit, args.save,
            Settings.from_env().sources_database_path, progress,
        ))
    except (InspectionError, MissingSessionError, InvalidCredentialsError, NetworkInspectionError) as error:
        if args.json:
            print(to_json({
                "source_identity": {"provider": "telegram", "requested": args.target},
                "metadata": {},
                "inspected_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "limit": args.limit,
                "findings": {},
                "warnings": [str(error)],
                "completion_status": "failed",
            }))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Expected credential parsing and OS-level setup errors stay concise.
        message = "Invalid Telegram configuration." if isinstance(error, (ValueError, OSError)) else f"Inspection failed ({type(error).__name__})."
        if args.json:
            print(to_json({
                "source_identity": {"provider": "telegram", "requested": args.target},
                "metadata": {}, "inspected_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "limit": args.limit, "findings": {}, "warnings": [message], "completion_status": "failed",
            }))
        else:
            print(f"Error: {message}", file=sys.stderr)
        return 1
    if args.json:
        print(to_json(result.as_dict()))
    else:
        print(f"Inspection complete: {result.metadata.get('title') or result.source_identity['username']}")
        print(f"Inspected at: {result.inspected_at}")
        print(f"Limit: {result.limit}")
        print(f"Telegram ID: {result.metadata.get('telegram_id') or 'unknown'}")
        print(f"Username: @{result.metadata['username']}" if result.metadata.get("username") else "Username: unknown")
        print(f"Description: {result.metadata.get('description') or 'unknown'}")
        print(f"Type: {result.metadata.get('source_type') or 'unknown'}")
        print(f"Participant count: {result.metadata.get('participant_count') if result.metadata.get('participant_count') is not None else 'unknown'}")
        print(f"Verified: {result.metadata.get('verified') if result.metadata.get('verified') is not None else 'unknown'}")
        discussion = result.metadata.get("linked_discussion")
        if discussion:
            print("Linked discussion: " + (discussion.get("title") or discussion.get("username") or discussion["telegram_id"]))
        else:
            print("Linked discussion: unknown")
        restrictions = result.metadata.get("access_restrictions")
        print(f"Access restrictions: {restrictions if restrictions is not None else 'unknown'}")
        print(f"Messages inspected: {result.findings['messages_inspected']} / {result.limit}")
        for key, value in result.findings.items():
            if key != "messages_inspected":
                print(f"{key.replace('_', ' ').title()}: {value}")
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        if result.registry_source_id is not None:
            print(f"Registry source ID: {result.registry_source_id}")
    return 0


def _run_explore(args: argparse.Namespace) -> int:
    progress = (lambda message: print(message, file=sys.stderr)) if args.json else print
    try:
        result = asyncio.run(_explore_with_existing_session(
            args.target, args.limit, args.save,
            Settings.from_env().sources_database_path, progress,
        ))
    except (InspectionError, MissingSessionError, InvalidCredentialsError, NetworkInspectionError) as error:
        if args.json:
            print(to_json({
                "source_identity": {"provider": "telegram", "requested": args.target},
                "explored_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "limit": args.limit, "messages_inspected": 0,
                "unique_references": 0, "total_occurrences": 0,
                "invalid_references_skipped": 0, "references": [],
                "warnings": [str(error)], "completion_status": "failed",
            }))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        message = "Invalid Telegram configuration." if isinstance(error, (ValueError, OSError)) else f"Exploration failed ({type(error).__name__})."
        if args.json:
            print(to_json({
                "source_identity": {"provider": "telegram", "requested": args.target},
                "explored_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "limit": args.limit, "messages_inspected": 0,
                "unique_references": 0, "total_occurrences": 0,
                "invalid_references_skipped": 0, "references": [],
                "warnings": [message], "completion_status": "failed",
            }))
        else:
            print(f"Error: {message}", file=sys.stderr)
        return 1
    if args.json:
        print(to_json(result.as_dict()))
    else:
        source_label = result.source_identity.get("title") or result.source_identity.get("username") or result.source_identity["telegram_id"]
        print(f"Exploration complete: {source_label}")
        print(f"Messages inspected: {result.messages_inspected} / {result.limit}")
        print(f"Unique references: {result.unique_references}")
        print(f"Total occurrences: {result.total_occurrences}")
        for reference in result.references:
            print(f"{reference['reference_type']}: {reference['normalized_target']} "
                  f"(occurrences: {reference['occurrence_count']})")
            for evidence in reference["provenance"]:
                print(f"  source {evidence['origin_source_id']}, message {evidence['origin_message_id']}, "
                      f"discovered {evidence['discovered_at']}")
        if result.warnings:
            print("Warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        if result.registry_summary:
            print(f"Candidate sources created: {result.registry_summary['candidate_sources_created']}")
            print(f"Provenance records processed: {result.registry_summary['provenance_records_saved']}")
    return 0


async def _search_with_existing_session(query, *, mode, entity_type, limit, registry_path, save, progress):
    query = validate_search_query(query, mode, entity_type, limit)
    if mode == "local":
        return await search_public(None, query, mode=mode, entity_type=entity_type,
                                   limit=limit, registry_path=registry_path, save=save, progress=progress)
    settings = Settings.from_env()
    require_existing_session(settings.session_path)
    if settings.api_id is None or not settings.api_hash:
        raise InvalidCredentialsError("TG_API_ID and TG_API_HASH are required; no login prompt will be opened.")
    client = create_client(settings)
    connected = False
    try:
        await client.connect()
        connected = True
        if not await client.is_user_authorized():
            raise InvalidCredentialsError("The existing Telegram session is not authorized; interactive login is disabled.")
        return await search_public(client, query, mode=mode, entity_type=entity_type,
                                   limit=limit, registry_path=registry_path, save=save, progress=progress)
    except Exception as error:
        if isinstance(error, (InspectionError, MissingSessionError, RegistryError)):
            raise
        from telegramlab.telegram.inspector import _raise_mapped_error
        _raise_mapped_error(error)
        raise SearchError(f"Telegram search failed ({type(error).__name__}).") from None
    finally:
        if connected:
            try:
                await client.disconnect()
            except Exception:
                pass


def _run_search(args: argparse.Namespace) -> int:
    mode = "local" if args.local else args.mode
    progress = lambda message: print(message, file=sys.stderr)
    try:
        response = asyncio.run(_search_with_existing_session(
            args.query, mode=mode, entity_type=args.entity_type, limit=args.limit,
            registry_path=Settings.from_env().sources_database_path,
            save=args.save, progress=progress))
    except (InspectionError, MissingSessionError, RegistryError, ValueError, OSError) as error:
        if args.json:
            print(to_json({"query": args.query, "mode": mode, "results": [],
                           "warnings": [str(error)], "completion_status": "failed"}))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(to_json(response.as_dict()))
    else:
        print(f"Telegram results: {response.telegram_returned}; local matches: {response.local_found}; "
              f"saved candidates: {response.candidates_saved}")
        for item in response.results:
            marker = "registered" if item.already_registered else "new"
            name = item.display_name or item.username or item.telegram_id or "unknown"
            print(f"{item.entity_type}: {name} (@{item.username or '-'}, ID {item.telegram_id or '-'}) "
                  f"[{item.origin}; {marker}]")
            if args.verbose:
                print(f"  method: {item.discovery_method}; discovered: {item.discovered_at}")
            for warning in item.warnings:
                print(f"  warning: {warning}")
        for warning in response.warnings:
            print(f"Warning: {warning}")
    return 0


async def _content_with_existing_session(query, *, kind, source, limit, local, save, index_path, progress):
    query, source = validate_content(query, kind, limit, source)
    if local:
        return await search_content(None, query, kind=kind, source=source, limit=limit,
                                    local=True, index_path=index_path, progress=progress)
    settings = Settings.from_env()
    require_existing_session(settings.session_path)
    if settings.api_id is None or not settings.api_hash:
        raise InvalidCredentialsError("TG_API_ID and TG_API_HASH are required; no login prompt will be opened.")
    client = create_client(settings)
    connected = False
    try:
        await client.connect()
        connected = True
        if not await client.is_user_authorized():
            raise InvalidCredentialsError("The existing Telegram session is not authorized; interactive login is disabled.")
        return await search_content(client, query, kind=kind, source=source, limit=limit,
                                    save=save, index_path=index_path, progress=progress)
    finally:
        if connected:
            try:
                await client.disconnect()
            except Exception:
                pass


def _run_content(args: argparse.Namespace) -> int:
    progress = lambda message: print(message, file=sys.stderr)
    try:
        settings = Settings.from_env()
        response = asyncio.run(_content_with_existing_session(
            args.query, kind=args.content_type, source=args.source, limit=args.limit,
            local=args.local, save=args.save, index_path=settings.content_database_path,
            progress=progress))
    except (InspectionError, MissingSessionError, ValueError, OSError) as error:
        if args.json:
            print(to_json({"query": args.query, "results": [], "warnings": [str(error)],
                           "completion_status": "failed"}))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(to_json(response.as_dict()))
    else:
        print(f"Results: {len(response.results)}; returned messages: {response.scanned}; saved: {response.saved}")
        for hit in response.results:
            print(f"{hit.source_channel}/{hit.message_id} [{hit.content_type}; {hit.match_type}] "
                  f"{hit.message_date or 'unknown date'} {hit.message_url or ''}")
            if hit.filename:
                print(f"  File: {hit.filename}")
            if hit.text:
                print(f"  {hit.text[:200].replace(chr(10), ' ')}")
        for warning in response.warnings:
            print(f"Warning: {warning}")
    return 0


async def _research_with_existing_session(args, settings, progress):
    if args.local:
        return await run_research(None, args.query, limit=args.limit, max_sources=args.max_sources,
                                  inspect=args.inspect, explore=args.explore, content=args.content,
                                  local=True, save=args.save, registry_path=settings.sources_database_path,
                                  content_path=settings.content_database_path,
                                  history_path=settings.research_database_path, progress=progress)
    require_existing_session(settings.session_path)
    if settings.api_id is None or not settings.api_hash:
        raise InvalidCredentialsError("TG_API_ID and TG_API_HASH are required; no login prompt will be opened.")
    client = create_client(settings)
    connected = False
    try:
        await client.connect()
        connected = True
        if not await client.is_user_authorized():
            raise InvalidCredentialsError("The existing Telegram session is not authorized; interactive login is disabled.")
        return await run_research(client, args.query, limit=args.limit, max_sources=args.max_sources,
                                  inspect=args.inspect, explore=args.explore, content=args.content,
                                  local=False, save=args.save, registry_path=settings.sources_database_path,
                                  content_path=settings.content_database_path,
                                  history_path=settings.research_database_path, progress=progress)
    finally:
        if connected:
            try:
                await client.disconnect()
            except Exception:
                pass


def _run_research(args: argparse.Namespace) -> int:
    try:
        response = asyncio.run(_research_with_existing_session(
            args, Settings.from_env(), lambda message: print(message, file=sys.stderr)))
    except (InspectionError, MissingSessionError, RegistryError, sqlite3.DatabaseError, ValueError, OSError) as error:
        if args.json:
            print(to_json({"query": args.query, "sources": [], "content_results": [],
                           "errors": [str(error)], "completion_status": "failed"}))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(to_json(response.as_dict()))
    else:
        print(f"Sources: {len(response.sources)}; inspected: {response.evidence_summary['sources_inspected']}; "
              f"explored: {response.evidence_summary['sources_explored']}; "
              f"content results: {response.evidence_summary['content_results']}")
        for source in response.sources:
            state = "inspected" if source.inspected else "discovered"
            print(f"{source.entity_type}: {source.display_name or source.username} "
                  f"(@{source.username}, ID {source.telegram_id or '-'}) [{state}]")
            for error in source.errors:
                print(f"  Warning: {error}")
        for hit in response.content_results:
            print(f"Content: {hit.source_channel}/{hit.message_id} [{hit.content_type}; {hit.match_type}] "
                  f"{hit.message_url or ''}")
        for warning in response.warnings:
            print(f"Warning: {warning}")
        for error in response.errors:
            print(f"Error: {error}", file=sys.stderr)
        if response.workflow_id is not None:
            print(f"Workflow ID: {response.workflow_id}")
    return 0 if response.completion_status == "completed" else 1


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        settings = Settings.from_env()
        print(f"Application version: {__version__}")
        print(f"Database path: {settings.database_path}")
        print(f"Database exists: {'yes' if database_exists(settings.database_path) else 'no'}")
        from telegramlab.telegram.client import session_file_path
        session_file = session_file_path(settings.session_path)
        print(f"Session file exists: {'yes' if session_file.is_file() else 'no'}")
        return 0
    if args.command == "source":
        try:
            return _run_source_command(args, Settings.from_env().sources_database_path)
        except RegistryError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "explore":
        return _run_explore(args)
    if args.command == "search":
        return _run_search(args)
    if args.command == "content":
        return _run_content(args)
    if args.command == "research":
        return _run_research(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
