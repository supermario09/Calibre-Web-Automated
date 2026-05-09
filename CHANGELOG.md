# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added

- **`CWA_DB` connection lifecycle management** (`scripts/cwa_db.py`): Added `close()`, `__del__`, and `__enter__`/`__exit__` so SQLite connections are released immediately rather than relying on GC. Previously every `CWA_DB()` instantiation leaked a file descriptor until garbage collected; under gevent with rapid page loads these accumulated until hitting the OS limit and crashing with `OSError: [Errno 24] Too many open files`.

- **`_log_activity()` helper** (`cps/web.py`): All six activity-logging call sites (magic shelf, email, wireless send, login, failed login, read) now use a single `_log_activity()` helper that opens `CWA_DB`, logs one event, and closes the connection — replacing six separate unclosed `CWA_DB()` instances per request path.

- **Docker container resource limits** (`docker-compose.yml`): Added `ulimits.nofile=65536`, `ulimits.nproc=32768`, `tmpfs /tmp:size=1g`, and `sysctls net.core.somaxconn=1024`. The tmpfs gives `ebook-convert` a RAM-backed temp dir (measurably faster conversions); the socket sysctl prevents dropped connections under load.

- **Per-book EPUB conversion button** (`cps/templates/detail.html`, `cps/editbooks.py`): Blue ⟳ button on book detail pages with non-EPUB formats. Queues conversion, shows spinning progress, auto-reloads page when complete (polls `/ajax/emailstat` by task ID). WiFi send button restricted to EPUB-only.

- **Cover always embedded in EPUB conversion** (`cps/tasks/convert.py`): `--cover cover.jpg` is now passed to `ebook-convert` even when "Embed Metadata" is disabled in settings, as long as the book has a cover. Previously the cover was only embedded when the full metadata embed option was on.

- **Duplicates page cache-based fast path**: When `scan_pending=0` in the CWA DB cache (no new books since last scan), the `/duplicates` page now reconstructs groups from the cache instead of re-scanning the entire library. Fetches only the known-duplicate book IDs (a small fraction of the library) with a single query + `selectinload`. Falls back to full scan if cache reconstruction fails. Page loads are now near-instant on repeat visits between ingests.

- **Performance indexes on Calibre DB hot columns**: `CREATE INDEX IF NOT EXISTS` applied on startup for `calibre.authors(sort)`, `calibre.data(book)`, and `calibre.books(author_sort)`. Eliminates full-table scans on queries that run on every page render and every duplicate scan.

- **Duplicate groups pagination**: The `/duplicates` page now loads groups in pages of 25 (configurable: 10 / 25 / 50 / 100 via a per-page selector). Previously all groups were rendered at once, causing slow JS and a "Select All" that would select every book in the entire library. Now "Select Duplicates (this page)" is scoped to the current page only, keeping DOM size and selection state manageable. Pagination controls (prev / next / first / last / per-page selector) are shown when there is more than one page. The route accepts `?page=N&per_page=N` query params.

### Fixed

- **`scripts/ingest_processor.py` — ingest lock blocked for 97s by refresh retries**: Previous fix changed the `reconnect-db` timeout from 5s to 30s and added 3 retries. When Flask was consistently busy, all 3 attempts timed out at 30s each = 97s of ingest lock held per book. Reverted to 5s timeout (fire-and-forget). Only `ConnectionError` (server truly down) is retried once; `ReadTimeout` (Flask busy) now fails immediately since retrying won't help. `invalidate-cache` and `queue-duplicate-scan` also reverted to 5s. Log level downgraded from WARN to INFO for these non-critical skips.

- **`cps/tasks/duplicate_scan.py` — double full library scan on every trigger**: The full-scan path called `find_duplicate_books(include_dismissed=False)` then `find_duplicate_books(include_dismissed=True)` — two complete library scans per trigger. Now calls once with `include_dismissed=True` and filters dismissed groups in Python via `filter_dismissed_groups()`. Same fix applied to the incremental-scan fallback path.

- **`cps/static/js/duplicates.js`** — scan progress polling interval reduced from 2s to 5s, cutting server requests by 60% during long scans.

- **`cps/db.py` — `order_authors()` crash + N+1 rewrite**: Fixed `AttributeError: 'NoneType' object has no attribute 'id'` crash on the homepage/book list when the Calibre DB has orphaned author sort values. Simultaneously rewrote the function to use two bulk `.in_()` queries (one for all sort values, one fallback for any unmatched IDs) instead of one query per author per book. On a 20k book library this reduces author queries from O(N×M) to O(1). All `None` results from the DB are now guarded.

- **`cps/duplicates.py` — eager loading + remove N+1 in grouping loop**: Added `selectinload` for `authors`, `languages`, `series`, `publishers`, and `data` on the books query in `find_duplicate_books_python()`. Previously each of these relationships triggered a lazy-load query per book (up to 100k extra queries on a 20k library). The per-book `calibre_db.order_authors([book])` call in the grouping loop is replaced with direct `book.author_sort` access (already in memory); `order_authors` is now called once per duplicate group for display ordering.

- **`cps/static/js/duplicates.js`**: Batch delete (and Delete Selected) button remained disabled even after selecting books, because clicking a book card did not register a selection — only clicking the tiny checkbox directly worked. Added a `.book-item` click handler that toggles the checkbox and fires its `change` event, so clicking anywhere on a book card now selects it and enables the action buttons.

- **`cps/db.py` (author sort loop)**: When an `author_sort` value (e.g. `"The New Yorker   Apr 04, 2005"`) has no matching row in the `Authors.sort` column, the code now `continue`s to the next author instead of `break`ing out of the loop. Previously `break` would silently abandon all remaining authors, causing incorrect display ordering for multi-author books. Log level downgraded from `log.error` → `log.debug` — these are permanent data quality issues (magazines/periodicals with date-based author sorts) that are handled gracefully and cannot be actioned, so they must not flood production logs at warning/error level.

- **`scripts/ingest_processor.py`**: Increased timeout on all three internal API calls from 5 s to 30 s (`refresh_cwa_session`, `invalidate_duplicate_cache`, `schedule_debounced_duplicate_scan`). Flask can be momentarily saturated during heavy ingest, causing read timeouts that were logged as warnings and silently skipped the DB refresh. Added retry-with-backoff (3 attempts: 2 s, 5 s, 10 s) to `refresh_cwa_session` so a transient busy-server doesn't permanently miss the refresh. The other two endpoints continue to fail gracefully without retry since they are fully fire-and-forget.

- **`scripts/ingest_processor.py` — ingest refresh timeout regression fixed**: The previous 30s×3-retry approach blocked the ingest lock for up to 97s per book when Flask was busy. Reverted to 5s fire-and-forget for all three internal endpoints (`reconnect-db`, `invalidate-cache`, `queue-duplicate-scan`). Only `ConnectionError` (server truly down) is retried once; `ReadTimeout` now fails immediately.

- **`cps/duplicates.py` — `get_unresolved_duplicate_count()` full-scan replaced with cache read**: The badge count shown in the sidebar was previously computed by calling `find_duplicate_books()` — a full 20k-book library scan — on every page render. Now reads directly from the CWA DB cache (O(1) lookup with dismissed-group filtering).

- **`cps/render_template.py` — shared `CWA_DB` instance per page render**: `cwa_update_notification()` and `translations_missing_notification()` now accept an optional `cwa_settings` dict. `render_title_template` creates one `CWA_DB`, shares its settings, and closes the connection in a `finally` block — reducing from 3 unclosed connections to 2 properly-closed ones per render.

- **WiFi send — EPUB only, reliable transfer**: Send to Device button now only appears when the book has EPUB format and always sends EPUB. Transfer uses send-all-then-wait with 120s final ACK timeout; `BrokenPipeError` and `WebSocketConnectionClosedException` mid-send are treated as successful completion (device signals done by closing the connection).

- **`cps/db.py`**: Reverted in-memory SQLite engine from `QueuePool` back to `StaticPool`.
  `QueuePool` gives each pool connection its own isolated in-memory database, meaning only
  the connection that ran `ATTACH DATABASE` could see the `calibre` and `app_settings` schemas.
  All other pool connections would fail with "no such table". `StaticPool` is required here
  because it forces all connections to share one underlying SQLite connection, making
  `ATTACH DATABASE` visible across the pool.

### Added

- **Conversion resource limits** to prevent `ebook-convert` / `kepubify` from OOM-killing the container:
  - Per-process virtual memory cap via `RLIMIT_AS` (default 1500 MB, configurable via `CWA_CONVERSION_MEMORY_MB`)
  - Per-book timeout (default 600 s / 10 min, configurable via `CWA_CONVERSION_TIMEOUT`) — process is killed and task marked failed cleanly on expiry
  - Applies to: single-book conversions (`cps/tasks/convert.py`), library-wide conversions (`scripts/convert_library.py`), and kepubify (`cps/subproc_wrapper.py`)
  - `gc.collect()` between books in library conversion to reclaim memory between iterations
- **Unit tests** (`tests/unit/test_db_pool.py`, `tests/unit/test_conversion_limits.py`) covering:
  - StaticPool shared-connection semantics and ATTACH DATABASE visibility
  - Demonstration that QueuePool breaks in-memory SQLite with attached schemas
  - `process_open` `limit_memory` flag wires `preexec_fn` correctly on Linux / no-ops on Windows
  - `_set_conversion_limits` calls `resource.setrlimit(RLIMIT_AS, ...)` and survives errors silently
  - Default and env-override values for `CWA_CONVERSION_MEMORY_MB` and `CWA_CONVERSION_TIMEOUT`
  - Watchdog timer (`_kill_after`) kills overrunning processes and cancels cleanly when process finishes first
  - Source-level guarantees: all three conversion paths have `limit_memory`, `preexec_fn`, and `gc.collect()`

---

## [4.5.0] — 2026-05-09

### Added

- **SQLite connection pooling**: `QueuePool` (5 base + 10 overflow) on `app.db`; PRAGMA optimizations (WAL, 10 MB cache, 32 MB mmap, 30 s busy timeout) applied on every connection via SQLAlchemy event listener
- **Batch duplicate deletion**: `TaskBatchDeleteDuplicates` async task with configurable batch size (1–50); new `/duplicates/batch-delete` endpoint with CSRF protection; batch delete button and modal in the duplicates UI; progress tracking via Tasks panel
- **Docker memory limits**: 8 GB hard limit / 4 GB reservation in `docker-compose.yml` and `docker-compose.yml.dev` to prevent OOM kills on NAS systems
