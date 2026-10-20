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

## Security precautions

Keep API credentials in environment variables or a secrets manager. Do not commit credentials, Telethon session files, local databases, or exports. A Telethon session can authenticate to the account; protect its filesystem permissions. Treat collected messages as private research data. Request only sources the account is authorized to access, respect Telegram rate limits and flood waits, and avoid unsolicited messaging or profiling. Logging uses key-value formatting and redacts common credential-shaped values; do not log message text or session contents.

## Development roadmap

1. Foundation: modular package, offline CLI, configuration, and safety defaults.
2. Add tested, explicit source discovery and incremental collection with per-source checkpoints, bounded retries, and Telegram limit handling.
3. Add link extraction and source relationship analysis over authorized data.
4. Add opt-in reporting and export formats, followed by carefully scoped integrations with SourceSeer and ShowTor.

Each stage should preserve the local session and database, add migration tests before schema changes, and make source scope and data handling explicit. Public research features and customer acquisition intelligence require separate review of source permissions, privacy, and applicable platform rules before implementation.
