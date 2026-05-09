# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Fixed

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
