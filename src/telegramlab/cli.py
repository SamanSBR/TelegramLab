"""Command-line interface for offline TelegramLab operations."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence

from telegramlab import __version__
from telegramlab.core.config import Settings
from telegramlab.storage.database import database_exists

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?hash|api[_-]?id|token|password|secret)\s*([=:])\s*([^\s,;]+)"),
    re.compile(r"\b\d{6,}:\w{20,}\b"),
)


class SecretRedactionFilter(logging.Filter):
    """Redact common credential assignments and bot-token-shaped strings."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in _SECRET_PATTERNS:
            if pattern is _SECRET_PATTERNS[0]:
                message = pattern.sub(r"\1\2[REDACTED]", message)
            else:
                message = pattern.sub("[REDACTED]", message)
        record.msg = message
        record.args = ()
        return True


def configure_logging() -> None:
    """Configure concise structured key-value logging with redaction."""
    handler = logging.StreamHandler()
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s level=%(levelname)s logger=%(name)s message=%(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegramlab", description="Local-first Telegram research utility")
    parser.add_argument("--version", action="version", version=f"telegramlab {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Show offline application and local file status")
    return parser


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
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
