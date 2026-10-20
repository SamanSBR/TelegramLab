"""Command-line interface for offline TelegramLab operations."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegramlab", description="Local-first Telegram research utility")
    parser.add_argument("--version", action="version", version=f"telegramlab {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status", help="Show offline application and local file status")

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


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        settings = Settings.from_env()
        print(f"Application version: {__version__}")
        print(f"Database path: {settings.database_path}")
        print(f"Database exists: {'yes' if database_exists(settings.database_path) else 'no'}")
        session_file = settings.session_path
        if session_file.suffix != ".session":
            session_file = session_file.with_suffix(".session")
        print(f"Session file exists: {'yes' if session_file.is_file() else 'no'}")
        return 0
    if args.command == "source":
        try:
            return _run_source_command(args, Settings.from_env().sources_database_path)
        except RegistryError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
