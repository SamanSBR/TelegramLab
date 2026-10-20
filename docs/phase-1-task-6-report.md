# Phase 1, Task 6: Telegram search and discovery

## Outcome

Implemented bounded public Telegram chat discovery and offline registry search through `telegramlab search`. Task 5.1's `@zenmaxai` normalization regression was verified by the existing mocked inspect and explore tests before implementation. Live Telegram requests were not used for verification.

## Architecture and files

- `src/telegramlab/telegram/search.py` (new): exact username resolution, one-request public chat search, local matching, filtering, deduplication, registry reconciliation, result model, and progress stages.
- `src/telegramlab/cli.py` (modified): search options, existing-session wrapper, JSON and human output, error handling.
- `src/telegramlab/sources/registry.py` (modified): atomic candidate insertion and additive version 3 `search_discoveries` migration.
- `tests/test_search.py` (new): deterministic mocked search and registry tests.
- `README.md` (modified): modes, examples, limitations, registry behavior, security boundaries, and errors.

## Telegram APIs

Exact `@username` and public username URL queries call Telethon `get_entity` once. Literal name, partial name, keyword, and multi-word queries call `contacts.SearchRequest(q=<full query>, limit=<bounded limit>)` once and use only public chats with usernames from its `chats` response. The API may omit matching public chats. No message search, member enumeration, joins, profile scraping, URL fetching, or messaging is performed. Exact user results are classified as users and excluded from candidate saving.

## Database changes

The first eligible `--save` creates migration version 3 and a `search_discoveries` table with source ID, original query, discovery method, and timestamp. Each source/query/method pair is unique. The source registry retains existing source rows, notes, metadata, status, and relationships. New public chats are saved as inactive, unverified candidates. A conflicting registered username and Telegram numeric ID or entity type prevents candidate insertion. The migration was tested against temporary databases only; the project registry was not migrated during this task.

## Verification

- Complete suite: **94 passed** with `.venv/bin/pytest -q`.
- Search tests cover exact usernames and user classification, names, partial and multi-word queries, offline local search, both-mode merging, filters, stable ID deduplication, ambiguous names, username conflicts, empty and invalid queries, limits, JSON, candidate saving, duplicate prevention, provenance, existing metadata and relationship preservation, flood waits, timeouts, missing sessions, and network isolation.
- Protected file sizes and SHA-256 before and after were identical:

| File | Size | SHA-256 |
| --- | ---: | --- |
| `telegram_lab.session` | 49,152 | `fb64f8ec2b94d7020b1d1e6f57f43784e07f0dd7e4ff3a1a82f145433c973f65` |
| `telegram_lab.db` | 606,208 | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` |
| `data/sources.db` | 49,152 | `2b4cf9c6d325b39f808482e7307e3674198012b49c98b32417aacf2f0b8da68b` |

Registry row counts before and after: `sources=1`, `source_relationships=0`, `schema_migrations=1`, `inspection_runs=1`; `PRAGMA foreign_key_check` returned no errors.

## Known limitations

Telegram's public discovery index is incomplete and server ranked. Search metadata is limited to fields returned by the discovery or exact entity response; descriptions and participant counts may be unavailable. A username-only local record can be matched by username when it lacks a numeric ID, while conflicting known IDs remain separate. Results without public usernames are excluded from keyword discovery and candidate saving. Search does not inspect private conversations or automatically expand queries.

## Recommended next task

Review search result quality with a small, explicitly authorized manual session check, then define any ranking or export requirements before further discovery features.
