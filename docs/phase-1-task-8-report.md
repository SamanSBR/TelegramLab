# Phase 1, Task 8: Unified Research Workflow

## Architecture

`telegramlab research` composes the existing Task 6 source search, Task 4 inspector, Task 5 reference explorer, and Task 7 content search. It reuses one authorized Telethon session per live run. The default makes only the bounded source-search request. Optional inspection and exploration run sequentially over at most `--max-sources` returned public sources with stable Telegram IDs. The inspector and explorer now accept an optional expected ID and stop before reading messages if a username resolves to a different source.

The response keeps source discovery evidence separate from completed inspection and exploration. It aggregates observed message counts, VLESS and Shadowsocks candidate counts, reference counts, and content result counts. Reference provenance and public message URLs remain in the nested service results. Content results are deduplicated by source ID and message ID. Source deduplication preserves distinct discovery methods and timestamps while keeping conflicting known IDs separate. Names mentioned in messages are not treated as verified identities, and configuration candidates are never marked working.

`--content` issues the Task 7 bounded global public-message search independently of source inspection. Telegram coverage is incomplete; an empty response is reported as inconclusive. `--local` reads saved source metadata, saved source-query provenance, and indexed content without constructing a Telegram client or creating missing databases. A missing source registry causes a clear error instead of recreation.

Expected per-source errors retain partial results and allow later bounded sources to continue. FloodWait stops all remaining Telegram stages without retrying. Cancellation propagates. No joins, messaging, downloads, external URL requests, member enumeration, private-chat discovery, recursive crawling, or scheduling were added.

## Implemented commands

```text
telegramlab research "کانفیگ رایگان"
telegramlab research "VLESS" --limit 10
telegramlab research "sing-box" --inspect
telegramlab research "proxy" --inspect --explore --max-sources 2
telegramlab research "Python" --content
telegramlab research "سامان صابری" --content
telegramlab research "VLESS" --json
telegramlab research "VLESS" --save
telegramlab research "VLESS" --local
```

`--limit` defaults to 20 and is capped at 100. `--max-sources` defaults to 3 and is capped at 10. No persistence occurs unless `--save` is set. `--json` prints one structured object on stdout, with progress on stderr.

## Files changed

- `src/telegramlab/research/workflow.py` (new): workflow orchestration, evidence model, deduplication, local provenance review, execution history.
- `src/telegramlab/cli.py`: `research` parser, existing-session wrapper, JSON and human output.
- `src/telegramlab/core/config.py`: configurable `TELEGRAMLAB_RESEARCH_DB_PATH`.
- `src/telegramlab/telegram/inspector.py`, `explorer.py`: optional expected-ID guard before message reads.
- `src/telegramlab/telegram/content.py`: Task 7 progress numbering corrected to `[1/4]` through `[4/4]`, with the final stage reported after saving.
- `tests/test_research_workflow.py` (new): deterministic mocked workflow tests.
- `README.md`: workflow commands, data handling, limits, and safety boundaries.
- `docs/phase-1-task-8-report.md` (new): this report.

Task 7 files present in the working tree remain uncommitted from the preceding task; they were not recreated or committed here.

## Database migrations and persistence

`--save` uses the existing additive source registry migrations for search discoveries, inspection summaries, and reference provenance. Task 7 content evidence uses the separate `data/content.db` index. Task 8 adds a version 1 `research_runs` table in a separate `data/research.db` file, created only for `--save`. Each run stores its query, times, options, completion status, and structured result, including partial or failed runs that reached the workflow. `TELEGRAMLAB_RESEARCH_DB_PATH` can relocate it; protected database and session paths are rejected. Existing research notes, source display names, and source status are preserved by the reused registry services. `--local` opens evidence databases read-only and does not migrate or recreate them.

## Verification

- Complete suite: **120 passed** with `.venv/bin/pytest -q`.
- `git diff --check`: passed.
- Mocked tests cover all eight combinations of optional stages, source and content deduplication, provenance, empty results, partial failures, username ID changes, JSON output, persistence and notes preservation, FloodWait, bounded execution, offline review, cancellation, missing registry protection, history path protection, and Task 7 progress completion.
- No live Telegram search was performed. No commit or push was made.

Protected file SHA-256 values matched this task's starting values:

| File | SHA-256 |
| --- | --- |
| `telegram_lab.session` | `2ae9073493029cfa711c8183dc8299248b004d95abced28a0ec3617eae06179a` |
| `telegram_lab.db` | `f27b4079bfe7a41ee9f358dcbd811ff42113547e236c9692f325c42b9a0379f6` |
| `data/sources.db` | `3884a77e37d19adf9e6ae970bffff396205d6764ed6261bf14d015d5ed9c65a6` |

## Known limitations

Telegram source and content search are server-ranked, incomplete, and access dependent. `--content` searches one bounded global page; it is not restricted to the sources returned by source discovery. Audio and other media metadata can be omitted by Telegram's index. A public username or permalink may later change or disappear. Local review includes only previously persisted evidence. Reference totals are summed per explored source and may include the same target across different sources. The workflow does not test configurations or assess whether a mention identifies a real person.
