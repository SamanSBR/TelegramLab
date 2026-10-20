# TelegramLab

TelegramLab is an independent, local-first foundation for ethical Telegram research. This phase establishes package structure, offline status reporting, configuration, and safety boundaries. It does not automate Telegram collection or messaging.

## Installation

Requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Telegram authentication continues to use the existing `telegram_lab.session` basename in the project directory by default. Telegram credentials are optional for offline commands and are read from `TG_API_ID` and `TG_API_HASH` only for inspection. Local paths can be set with `TELEGRAMLAB_DB_PATH`, `TELEGRAMLAB_SOURCES_DB_PATH`, and `TELEGRAMLAB_SESSION_PATH`; relative paths resolve from the project directory.

## Architecture

`src/telegramlab/` separates core settings and models, Telethon client setup, storage, collectors, source registry, link extractors, research workflows, campaigns, and exports. The reference explorer reuses the existing Telethon client, URL entity extraction, and source registry. Existing top-level learning and collection scripts remain available.

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

### Read-only Telegram inspection

The inspector reads one explicitly supplied public username or an existing marked dialog ID for an authorized group using the existing Telethon session. Numeric IDs are resolved only through the current session's accessible entity cache; no join or invite-link flow is attempted. It never logs in interactively, follows links, downloads media, sends messages, or stores message text. Default limit is 30 and the maximum is 100.

```bash
telegramlab inspect @ExampleChannel
telegramlab inspect @ExampleChannel --limit 50 --json
telegramlab inspect @ExampleChannel --save
telegramlab inspect -1001234567890 --save  # already accessible dialog ID
```

The session defaults to the project-root `telegram_lab.session` file. Relative `TELEGRAMLAB_SESSION_PATH` values are resolved from the project root. If the session is missing or unauthorized, inspection stops without a login prompt. `--save` writes source metadata and an aggregate inspection summary to `data/sources.db`; it preserves existing manual metadata and notes, stores an available numeric Telegram ID, and leaves source status unchanged. Inspection summaries contain counts and dates only. The registry applies an additive versioned migration when saving the first inspection summary.

Inspection counts messages containing URLs, Telegram references, external URLs, and VLESS or Shadowsocks URI candidates. Configuration URIs are never parsed or printed; URI values embedded in the returned public description are redacted. A Telegram flood wait is reported and ends the run without retrying.

### Telegram reference exploration

The explorer reads up to 100 recent accessible messages from one explicitly selected source, with a default limit of 30. It uses the existing authenticated session and does not prompt for login.

```bash
telegramlab explore @ExampleChannel
telegramlab explore @ExampleChannel --limit 100
telegramlab explore @ExampleChannel --json
telegramlab explore @ExampleChannel --save
```

It reports unique references and occurrence counts for user mentions, channel references, message permalinks, external websites, GitHub and GitLab repositories, subscription URL candidates, and unknown references. A `t.me/<username>` link alone remains an unknown reference because a username may identify a user or a channel. Private invite links are omitted. Each finding retains its originating Telegram source ID, message ID, discovery time, type, and normalized target. JSON output includes the per-message provenance.

The explorer never follows discovered URLs, joins sources, sends messages, downloads media, or stores message text. `--save` writes candidates and per-message evidence to `data/sources.db` using an additive schema migration. Candidates remain unverified and inactive; ambiguous Telegram reference types use separate registry identities. Existing source notes and metadata are preserved. Telegram flood waits stop the run without automatic retries.

## Security precautions

Keep API credentials in environment variables or a secrets manager. Do not commit credentials, Telethon session files, local databases, or exports. A Telethon session can authenticate to the account; protect its filesystem permissions. Treat collected messages as private research data. Request only sources the account is authorized to access, respect Telegram rate limits and flood waits, and avoid unsolicited messaging or profiling. Logging uses key-value formatting and redacts common credential-shaped values; do not log message text or session contents.

## Development roadmap

1. Foundation: modular package, offline CLI, configuration, and safety defaults.
2. Add explicit incremental synchronization with per-source checkpoints, bounded retries, and Telegram limit handling.
3. Add source relationship review and analysis over authorized data, with evidence and source permission checks.
4. Add opt-in reporting and export formats, followed by carefully scoped integrations with SourceSeer and ShowTor.

Each stage should preserve the local session and database, add migration tests before schema changes, and make source scope and data handling explicit. Public research features and customer acquisition intelligence require separate review of source permissions, privacy, and applicable platform rules before implementation.
