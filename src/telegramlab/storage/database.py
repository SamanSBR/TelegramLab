"""Read-only database presence checks; no schema changes are performed here."""

from pathlib import Path


def database_exists(path: Path) -> bool:
    """Return whether the configured SQLite database file exists."""
    return path.is_file()
