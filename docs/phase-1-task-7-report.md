# Phase 1, Task 7: Public Content Search Engine

## Outcome

Added `telegramlab content search` for bounded, read-only search of accessible public broadcast channel messages and media metadata. No live Telegram search was run during development. All Telegram behavior was verified with mocked clients.

## Files changed

- `src/telegramlab/telegram/content.py`: Telegram search, public-channel filtering, evidence model, Unicode matching, SQLite content index, deduplication, and progress.
- `src/telegramlab/cli.py`: `content search` command, options, existing-session wrapper, output, and error handling.
- `src/telegramlab/core/config.py`: optional `TELEGRAMLAB_CONTENT_DB_PATH`, defaulting to `data/content.db`.
- `tests/test_content.py`: deterministic mocked Telegram and SQLite tests.
- `README.md`: command usage, boundaries, storage, matching, and limitations.
- `docs/phase-1-task-7-report.md`: this report.

## Architecture and capabilities

Global search sends one Telethon `messages.SearchGlobalRequest` with a maximum server limit of 100. `--source` resolves one public username and sends one `messages.SearchRequest` for that broadcast channel. Results from private channels, groups, and user dialogs are excluded. Search types are `message`, `document`, `photo`, `video`, `audio`, and `all`; the default is `all`. Audio uses an unfiltered bounded server page, then selects music and voice messages locally. Telegram's document, photo, and video server filters are used directly. There is no recursive retrieval, pagination, joining, or media download.

Each result records source channel and numeric ID, message ID and date, public URL, original query, discovery timestamp, content type, match type, original text, available filename, and MIME metadata. Exact text means a normalized query with word boundaries; partial text means a substring within a larger token. Filename and metadata are separate labels. Persian and Arabic letter variants, presentation forms, diacritics, and zero-width joiners are normalized only for matching. Original text remains unchanged. Real-name matches are mentions, never verified identities.

`--save` creates a separate optional SQLite index at `data/content.db` (configurable). Versioned `content_hits` and `content_discoveries` tables are additive. Content is unique by Telegram source ID and message ID; query provenance is unique by source ID, message ID, and query. `--local` opens the index read-only and makes no Telegram request. It returns only previously saved public results. The index refuses paths that overlap the configured or default protected databases or the session file.

Progress reports request and result counts on stderr. Missing sessions, authorization failure, inaccessible sources, timeouts, SQLite errors, and flood waits are surfaced. Flood waits stop the request without retrying. JSON stdout is separate from progress.

## Verification

- Complete suite: **100 passed** with `.venv/bin/pytest -q`.
- `git diff --check`: passed.
- Tests cover global and source request types, bounded limit, public-only selection, deduplication, evidence, filename and text matching, Persian/Arabic normalization, photo/video/audio classification, local offline mode, index migration and query provenance, protected paths, flood waits, and invalid input.
- Protected file SHA-256 values were identical before and after the task:

| File | SHA-256 |
| --- | --- |
| `telegram_lab.session` | `9f66fbbeea323b198a76606129a2fcd15ee3b867e5213213e647503972214876` |
| `telegram_lab.db` | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` |
| `data/sources.db` | `3884a77e37d19adf9e6ae970bffff396205d6764ed6261bf14d015d5ed9c65a6` |

## Known limitations

Telegram global search is ranked, incomplete, and dependent on account access and indexing. A single bounded page can return fewer relevant results after public-channel and type filtering. Some filename or media metadata searches may not be indexed by Telegram even though saved local records are searchable. The local index only contains explicitly saved results and does not update deleted or edited Telegram messages automatically. Public usernames and message URLs can change or disappear. No claim of exhaustive global coverage is made.
