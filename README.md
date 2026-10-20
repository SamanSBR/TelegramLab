# TelegramLab

TelegramLab is an independent, local-first foundation for ethical Telegram research. This phase establishes package structure, offline status reporting, configuration, and safety boundaries. It does not automate Telegram collection or messaging.

## Installation

Requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Telegram authentication continues to use the existing `telegram_lab.session` basename in the current working directory by default. Telegram credentials are optional for offline commands and are read from `TG_API_ID` and `TG_API_HASH` only when a future Telegram operation is requested. Local paths can be set with `TELEGRAMLAB_DB_PATH` and `TELEGRAMLAB_SESSION_PATH`.

## Architecture

`src/telegramlab/` separates core settings and models, Telethon client setup, storage, collectors, source registry, link extractors, research workflows, campaigns, and exports. The extension modules are placeholders; they do not collect or process Telegram content yet. Existing top-level learning and collection scripts remain available.

## CLI

```bash
telegramlab --help
telegramlab status
```

`status` runs offline and reports the version, configured database path, and whether the database and session files exist. It does not read database rows or display credentials or message content. `python -m telegramlab` is also supported.

### Source registry

The source registry is offline and uses a separate SQLite database at `data/sources.db`. Set `TELEGRAMLAB_SOURCES_DB_PATH` to use another registry path. Registry initialization never uses the existing `telegram_lab.db`; a path that resolves to that database is rejected. Adding a source records it as a `candidate` and records its discovery method (default `manually_added`). Adding an existing identity is idempotent and preserves its original metadata.

```bash
telegramlab source add --provider telegram --type channel \
  --identifier @ExampleChannel --category configurations
telegramlab source list
telegramlab source search "config"
telegramlab source show 1
telegramlab source update 1 --name "Example source" --status active
telegramlab source pause 1
telegramlab source resume 1
telegramlab source stats
```

Append `--json` to supported source commands for JSON output. Telegram public username inputs such as `@ExampleChannel`, `https://t.me/ExampleChannel`, and `t.me/ExampleChannel` normalize to the same username identity within a provider and source type. This does not verify availability or permanence. A separately supplied `platform_id` is available for a stable provider identifier legitimately obtained later. The registry does not visit URLs, access Telegram, join private sources, infer relationships, or collect content. Relationships can be recorded through the registry API only when an evidence reference is supplied; relationship types are extensible slugs.

## Security precautions

Keep API credentials in environment variables or a secrets manager. Do not commit credentials, Telethon session files, local databases, or exports. A Telethon session can authenticate to the account; protect its filesystem permissions. Treat collected messages as private research data. Request only sources the account is authorized to access, respect Telegram rate limits and flood waits, and avoid unsolicited messaging or profiling. Logging uses key-value formatting and redacts common credential-shaped values; do not log message text or session contents.

## Development roadmap

1. Foundation: modular package, offline CLI, configuration, and safety defaults.
2. Add tested, explicit source discovery and incremental collection with per-source checkpoints, bounded retries, and Telegram limit handling.
3. Add link extraction and source relationship analysis over authorized data.
4. Add opt-in reporting and export formats, followed by carefully scoped integrations with SourceSeer and ShowTor.

Each stage should preserve the local session and database, add migration tests before schema changes, and make source scope and data handling explicit. Public research features and customer acquisition intelligence require separate review of source permissions, privacy, and applicable platform rules before implementation.
