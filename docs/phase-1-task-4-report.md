# Phase 1, Task 4: Telegram Channel Inspector

## Architecture changes

Added a read-only asynchronous inspector under `telegramlab.telegram.inspector`. It resolves exactly one supplied public username or marked dialog ID, classifies a channel/group, requests available full-channel metadata, and iterates no more than the requested number of recent accessible messages. The inspector does not use Telethon's interactive `start()` flow, join a source, follow links, download media, send messages, or persist message text.

`telegramlab.extractors.links` now extracts plain URLs and Telegram URL/text-link entities, slicing URL entities using Telegram's UTF-16 offsets and deduplicating results. It counts message-level aggregates and recognizes VLESS and Shadowsocks prefixes without parsing or returning URI contents. Configuration URI strings embedded in a public description are redacted before output or storage.

When `--save` is requested, `telegramlab.sources.registry.save_inspection_record` performs an additive versioned migration and writes a separate aggregate run record in `data/sources.db`. Existing source fields, status, and notes are preserved; missing metadata may be filled, the stable Telegram ID is recorded, and `last_checked_at` is updated only after message inspection completes. Username-to-numeric-ID conflicts stop the save. The registry schema migration creates `schema_migrations` and `inspection_runs`; it does not alter existing tables or rows.

The existing Telethon client factory now requires the existing session file before constructing a client. Default and relative session paths resolve from the project root. Authorization is checked after `connect()` with no interactive login fallback.

## Files created

- `src/telegramlab/telegram/inspector.py`
- `tests/test_inspector.py`
- `docs/phase-1-task-4-report.md`

## Files modified

- `src/telegramlab/cli.py` — added `inspect` command, JSON output, concise errors, and progress.
- `src/telegramlab/core/config.py` — project-root default and relative path resolution.
- `src/telegramlab/telegram/client.py` — existing-session validation and exact session file resolution.
- `src/telegramlab/extractors/links.py` — plain/entity URL detection, UTF-16 handling, and deduplication.
- `src/telegramlab/sources/registry.py` — additive inspection migration and safe persistence/conflict handling.
- `tests/test_config_and_logging.py` — relative path test.
- `README.md` — inspector examples and behavior.

Legacy scripts were not modified. `pyproject.toml` required no changes.

## CLI examples

```bash
telegramlab inspect @ExampleChannel
telegramlab inspect @ExampleChannel --limit 50 --json
telegramlab inspect @ExampleChannel --save
telegramlab inspect -1001234567890 --save  # accessible marked dialog ID
```

The limit defaults to 30 and accepts values from 1 through 100. Progress is emitted at four actual stages. For JSON output, progress goes to stderr and the structured inspection result goes to stdout. Flood waits report the requested duration and stop without retrying.

## Tests and validation

- Editable installation: `.venv/bin/python -m pip install -e '.[dev]'` — passed.
- Existing CLI help/status and source registry list/stats commands — passed.
- `telegramlab inspect --help` — passed.
- Full suite: `.venv/bin/pytest -q` — **51 passed**.
- Tests use mocked Telethon clients and temporary databases. A socket connection guard is active for inspector unit tests; no real Telegram request was made.
- Legacy script diff check and import coverage — passed; legacy files are unchanged.
- No real Telegram inspection was run.

## Security considerations

The inspector checks for an existing `.session` file before constructing Telethon's client, requires the saved authorization to already be valid, and never prompts for credentials. It reports no API credentials, session content, message text, sender details, member lists, extracted URLs, or full configuration URIs. Inspection does not follow URLs, access HTTP, download media, join sources, or send messages. Registry persistence stores only source metadata and aggregate counts/dates. Only explicitly supplied sources are examined; private access depends on authorization already present in the reused session.

## Known limitations

- Username inputs must be public usernames. Marked numeric dialog IDs are supported for sources resolvable from the existing account/session cache; invite links are rejected and never joined.
- Full description and linked discussion metadata are requested only for Telegram `Channel` entities; unavailable fields remain `null`/unknown.
- Message inspection counts URL/config presence per message, not unique domains or individual occurrences. It does not validate VLESS/Shadowsocks configurations.
- URL detection is intentionally lightweight; it does not resolve redirects or discover bare domains without URL schemes.
- Numeric ID conflicts are detected for an existing normalized username. Numeric-ID-only records do not imply a username or public canonical URL.
- The additive inspection migration is applied on the first successful `--save`, not during read-only inspection.

## Data preservation verification

SHA-256 and sizes captured before implementation matched the final checks. No protected file contents were printed.

| Protected file | Before SHA-256 | After SHA-256 | Size |
|---|---|---|---:|
| `telegram_lab.session` | `11a5df6e4e3121479fb7f90ea457785ca798081f18f746e9299b9dcdadf4f314` | `11a5df6e4e3121479fb7f90ea457785ca798081f18f746e9299b9dcdadf4f314` | 49,152 bytes |
| `telegram_lab.db` | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` | 606,208 bytes |

## Recommended next task

Add a user-reviewed inspection history view/export over the aggregate `inspection_runs` records, with retention controls and explicit source scope. Keep raw messages and configuration URIs out of persistence, and add a tested backup/restore path for the separate source registry before expanding live collection.
