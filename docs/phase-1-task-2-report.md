# Phase 1, Task 2: Modular Architecture Report

## Files created

- `.gitignore`
- `pyproject.toml`
- `README.md`
- `src/telegramlab/__init__.py`
- `src/telegramlab/__main__.py`
- `src/telegramlab/cli.py`
- `src/telegramlab/core/__init__.py`
- `src/telegramlab/core/config.py`
- `src/telegramlab/core/models.py`
- `src/telegramlab/telegram/__init__.py`
- `src/telegramlab/telegram/client.py`
- `src/telegramlab/storage/__init__.py`
- `src/telegramlab/storage/database.py`
- `src/telegramlab/collectors/__init__.py`
- `src/telegramlab/collectors/messages.py`
- `src/telegramlab/sources/__init__.py`
- `src/telegramlab/sources/registry.py`
- `src/telegramlab/extractors/__init__.py`
- `src/telegramlab/extractors/links.py`
- `src/telegramlab/research/__init__.py`
- `src/telegramlab/campaigns/__init__.py`
- `src/telegramlab/exports/__init__.py`
- `tests/test_cli.py`
- `tests/test_config_and_logging.py`
- `tests/test_legacy_imports.py`
- `docs/phase-1-task-2-report.md`

## Files modified

None of the original scripts were modified. `main.py`, `lesson02.py`, `collector.py`, and `my_activity.py` remain in place and import successfully.

## Architecture summary

The new `src`-layout package separates core settings and data types, Telethon client construction, storage presence checks, collection, source registration, link extraction, research, campaigns, and exports. The latter workflow modules are extension points only; no automated collection or future research feature was implemented.

Configuration is environment-backed: `TG_API_ID` and `TG_API_HASH` are optional for offline operation; `TELEGRAMLAB_DB_PATH` and `TELEGRAMLAB_SESSION_PATH` optionally select local paths. Defaults preserve the existing `telegram_lab.db` and Telethon `telegram_lab` session basename in the current working directory. The `telegramlab status` command and `python -m telegramlab status` work offline and disclose only version, configured DB path, and database/session file presence. Logging uses key-value formatting and a filter for common credential-shaped values.

`pyproject.toml` declares Python 3.11+, Telethon, pytest development dependency, and the `telegramlab` console command. `.gitignore` excludes session files, SQLite files and journals, environment secrets, virtual environments, and Python generated files.

## Test results

- Editable install: `.venv/bin/python -m pip install -e '.[dev]'` — passed.
- Test suite: `.venv/bin/pytest -q` — **6 passed**.
- `.venv/bin/telegramlab --help` — passed.
- `.venv/bin/telegramlab status` — passed offline; detected both existing local files.
- `.venv/bin/python -m telegramlab status` — passed offline.
- Import checks for all four existing scripts — passed.
- Runtime used for validation: Python 3.14; package metadata requires Python 3.11 or newer.

## Data preservation verification

SHA-256 hashes and sizes were recorded before implementation and checked again afterward. The protected artifacts were not opened for content inspection by this task.

| Artifact | Before SHA-256 | After SHA-256 | Size unchanged |
|---|---|---|---|
| `telegram_lab.session` | `11a5df6e4e3121479fb7f90ea457785ca798081f18f746e9299b9dcdadf4f314` | `11a5df6e4e3121479fb7f90ea457785ca798081f18f746e9299b9dcdadf4f314` | 49,152 bytes |
| `telegram_lab.db` | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` | 606,208 bytes |

No session was created. The existing SQLite database was not migrated or modified.

## Known limitations

- The status command checks file presence only; it does not validate database integrity, session authorization, or connectivity.
- Secret redaction covers common credential assignments and bot-token-shaped values, not every possible secret format. Avoid logging credentials or private content in the first place.
- Collection, source discovery, link extraction, exports, campaigns, and external integrations are placeholders and intentionally inactive.
- The existing scripts remain standalone and retain their existing behavior; they have not yet been migrated to call into package modules.
- There is no per-source synchronization state, rate-limit handling, or schema migration system in this foundation task.

## Recommended next task

Task 3 should add explicit, permission-scoped source discovery and incremental message synchronization behind the new modules, with per-source checkpoints, Telegram flood-wait handling, and tests using mocked Telethon responses and temporary SQLite databases. Preserve the current database schema and session path; make any schema change a separately reviewed migration.
