# Phase 1, Task 3: Persistent Source Registry

## Files created

- `src/telegramlab/sources/registry.py` — SQLite registry operations, validation, URL normalization, and record types.
- `tests/test_source_registry.py` — deterministic registry and preservation tests.
- `data/sources.db` — new, separate, initialized source registry database.
- `docs/phase-1-task-3-report.md` — this report.

## Files modified

- `src/telegramlab/cli.py` — added the offline `source` command group and JSON output.
- `src/telegramlab/core/config.py` — added `TELEGRAMLAB_SOURCES_DB_PATH` with default `data/sources.db`.
- `tests/test_cli.py` — added offline registry CLI coverage.
- `README.md` — documented registry use, path configuration, and behavior.

The four legacy scripts (`main.py`, `lesson02.py`, `collector.py`, `my_activity.py`) were not modified.

## Database schema

The separate `data/sources.db` contains:

- `sources`: requested source metadata fields plus `identity_key` for duplicate protection and nullable `platform_id` for a stable provider ID obtained legitimately later. Identity uniqueness is enforced on `(provider, source_type, identity_key)`. Status is constrained to `candidate`, `active`, `paused`, `inaccessible`, or `rejected`; new records default to `candidate`.
- `source_relationships`: source pair, extensible relationship type, discovery timestamp, and required evidence reference. Duplicate relationship triples are constrained, and self-links are rejected. Foreign keys are enabled.

Provider, source type, category, discovery method, and relationship type use validated extensible slugs. Categories are not restricted to a business-specific list. Adding a duplicate source or relationship preserves the original metadata/evidence.

Telegram public usernames are case-insensitive and normalize `@ExampleChannel`, `https://t.me/ExampleChannel`, and `t.me/ExampleChannel` to the same canonical username and URL within the same provider and source type. Other Telegram URL paths retain case-sensitive path data. Username identity is not treated as permanent; `platform_id` is separate. No source availability is verified, and relationships are only stored when explicitly supplied with evidence.

The registry refuses a configured path that resolves to the existing `telegram_lab.db`. It does not open or migrate that database.

## CLI commands

```text
telegramlab source add --provider telegram --type channel --identifier @ExampleChannel --category configurations
telegramlab source list [--status STATUS] [--provider PROVIDER] [--category CATEGORY]
telegramlab source show ID
telegramlab source search QUERY
telegramlab source update ID [--name NAME] [--description TEXT] [--url URL] [--category CATEGORY] [--notes TEXT] [--discovery-method METHOD] [--discovered-from REF] [--status STATUS] [--platform-id ID]
telegramlab source pause ID
telegramlab source resume ID
telegramlab source stats
```

Supported source commands accept `--json`. Ordinary registry validation and lookup errors are concise and do not print stack traces. Commands work offline and do not access Telegram or visit URLs. `TELEGRAMLAB_SOURCES_DB_PATH` can select a different registry database.

## Tests and validation

- Editable install: `.venv/bin/python -m pip install -e '.[dev]'` — passed.
- Existing and new pytest suite: `.venv/bin/pytest -q` — **23 passed**.
- Existing CLI: `.venv/bin/telegramlab --help` and `.venv/bin/telegramlab status` — passed.
- Source CLI: add, list, search, show, update, pause, resume, and stats — passed against a temporary database.
- Source CLI network guard: passed; a test makes socket connection attempts fail.
- Legacy imports: `main.py`, `lesson02.py`, `collector.py`, and `my_activity.py` — passed; no diffs to those files.
- The default registry database was initialized and contains `sources` and `source_relationships` tables.

## Data preservation checks

SHA-256 hashes and file sizes captured before implementation matched the final checks:

| Protected artifact | Before SHA-256 | After SHA-256 | Size |
|---|---|---|---:|
| `telegram_lab.session` | `11a5df6e4e3121479fb7f90ea457785ca798081f18f746e9299b9dcdadf4f314` | `11a5df6e4e3121479fb7f90ea457785ca798081f18f746e9299b9dcdadf4f314` | 49,152 bytes |
| `telegram_lab.db` | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` | 606,208 bytes |

Protected artifact contents were not printed or inspected. No Telegram client was created, no network access was performed, and no messages or external content were accessed.

## Known limitations

- Registry status is operator-entered; candidate or active status does not imply Telegram or website verification.
- URL normalization is intentionally conservative and does not resolve redirects, aliases, invite links, or username changes.
- `platform_id` is stored separately but is not used to merge records or guarantee provider identity.
- Relationships can be created through the Python registry API; this task does not add a relationship CLI command or infer relationships.
- Search covers stored metadata with SQLite `LIKE`; it is not full-text search.
- The registry has no automatic source discovery, scraping, scheduling, configuration testing, messaging, dashboard, or external integrations.

## Recommended next task

Add explicit, permission-scoped source inspection metadata and incremental sync state for selected sources, using mock Telegram adapters and isolated temporary databases. Define per-source checkpoints and bounded Telegram flood-wait handling before implementing any live collection. Keep source verification distinct from manual registry status, and preserve the existing session and research database.
