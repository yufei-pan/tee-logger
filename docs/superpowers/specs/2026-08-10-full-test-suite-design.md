# Full Test Suite Design — tee-logger

Date: 2026-08-10  
Status: Approved for planning  
Scope: Near-exhaustive automated tests for `Tee_Logger` (pytest + CI)

## Context

`tee-logger` is a single-module library (`src/Tee_Logger.py`, ~840 lines) that writes dated log files, optionally tees colored output to stdout, supports in-place compression (`gzip` / `bz2` / `xz` / `zstd`), and runs startup log maintenance (age-based delete and `.tar.xz` archival).

Today’s coverage is thin:

- `tests/test_doctest.py` — runs module doctests via unittest
- `tests/test_caller_info.py` — caller attribution, `_log_dir_date_key`, duplicate handlers, one gzip binary-mode check

README still documents `unittest discover`. `.github/workflows/` exists but has no workflow file.

## Goals

1. Near-exhaustive behavioral coverage of the public API and important configuration axes (compression × binary/text × collapse, cleanup, tee vs file-only, caller attribution, helpers, CLI).
2. Migrate existing unittest tests into a single pytest style and discovery story.
3. Run locally with a clear pytest command and in GitHub Actions on Python 3.9, 3.11, 3.12, and 3.14.
4. Keep tests isolated (no leftover `/tmp` pollution, no logger handler leaks).

## Non-goals

- CI performance gates from the existing benchmark scripts
- Coverage-percentage or mutation-testing gates
- Windows/macOS CI runners (Unix symlink asserts skip off-Unix when run locally)
- Changing `requires-python` or rewriting production compression/cleanup “for testability” beyond the minimum (prefer zero production changes)
- Exhaustive system-`tar` vs every OS quirk — use real success paths plus a forced Python `tarfile` fallback test

## Approach

Feature-module pytest suite with shared fixtures and selective markers.

Rationale: the compression matrix is large, but concerns (helpers, init, API, compression, cleanup, CLI) stay navigable. Markers/`skipif` handle zstd availability without splitting the repo into unit/integration packages.

## Tooling

- Add a `dev` optional dependency group (or equivalent) in `pyproject.toml` including `pytest`
- `pytest-cov` may be available for local use; it is **not** a CI gate
- Configure `[tool.pytest.ini_options]`:
  - `testpaths = ["tests"]`
  - `pythonpath = ["src"]` (or install the package editable in CI/local)
  - register markers (at least `zstd`)
  - enable doctests for `src/Tee_Logger.py` (ini option and/or explicit collection)
- Primary local command: `uv run pytest` or `python -m pytest`
- Remove reliance on `python -m unittest discover` as the documented primary path; update README Testing section

## Layout

```
tests/
  conftest.py              # tmp log roots, teeLogger factory, read helpers, handler cleanup
  test_helpers.py          # abbreviate_filename, pretty_format_table, printWithColor, _log_dir_date_key
  test_caller.py           # getCallerInfo + auto/explicit callerStackDepth (migrated)
  test_init_config.py      # noLog, programName, suppressPrintout, /dev/null, compression fallbacks
  test_logging_api.py      # info/error/ok/log + tee* variants, levels, printTable
  test_compression.py      # backend × binary_mode × collapse matrix; round-trip read
  test_handlers.py         # duplicate handler replacement, latest symlink, BinFileHandler modes
  test_cleanup.py          # delete-by-years + compress-to-tar.xz; tarfile fallback
  test_cli.py              # -V/--version
```

Delete (after migration): `tests/test_doctest.py`, `tests/test_caller_info.py`.

## Shared fixtures and isolation

Contract for every file-writing test:

1. Use pytest `tmp_path` as `systemLogFileDir` for normal tests — do not rely on shared `/tmp/{program}_log` leftovers.
2. Exception: the unwritable-primary → `/tmp` fallback test may create files under `/tmp`; it must use a unique `programName` and remove that tree in teardown.
3. Use a unique `programName` per test (factory-generated is fine) so `logging.getLogger(name)` instances do not collide.
4. Factory or autouse fixture closes/removes file handlers after each test to avoid global logging state leaks.
5. Helpers to locate the active log file under the dated layout and to read plain or compressed content for assertions.

Cleanup tests that would only schedule background work should prefer invoking the sync compress/delete helpers directly, or wait with a short timeout and fail clearly if work never finishes.

## Coverage matrix

### Helpers (`test_helpers.py`)

- `abbreviate_filename`: short names, long names, line-number shortening
- `pretty_format_table`: empty and non-empty tables, header behavior covered by existing semantics
- `printWithColor`: color on/off; redirected stdout capture
- `_log_dir_date_key`: plain date, `.tar.xz` strip, `.txt` not stripped (migrated cases)

### Caller (`test_caller.py`)

- Auto mode through a user wrapper: log shows wrapper line, not `Tee_Logger` internals
- Direct call line match
- Explicit positive `getCallerInfo` depth
- Per-call `callerStackDepth` override on log methods

### Init / config (`test_init_config.py`)

- `noLog=True` → NullHandler, no log files created
- `systemLogFileDir` in `{/dev/null, /dev/stdout, /dev/stderr}` forces noLog
- Default `programName` derived from caller when omitted
- `in_place_compression=True` (and `...` if still accepted) → `xz`
- Invalid compression value → warning path + `xz`, logging still works
- `zstd` when `compression.zstd` unavailable → falls back to `xz` (force `ImportError` where needed)
- `collapse_single_day_logs` defaults to `True` only when in-place compression is set
- Unwritable primary directory → `/tmp` fallback (non-writable path or mocked failure)

### Logging API (`test_logging_api.py`)

For `info` / `error` / `ok` / `log` and matching `tee*` methods:

- Message is present in the log file
- With `suppressPrintout=False`, tee variants appear on stdout (capture); non-tee methods do not
- `printTable` formats and logs
- Log line shape approximately: `timestamp [LEVEL] [file:line] message`

### Compression (`test_compression.py`)

Parametrize:

| Axis | Values |
|------|--------|
| backend | `None`, `gzip`, `bz2`, `xz`, `zstd` |
| `binary_mode` | `True`, `False` |
| `collapse_single_day_logs` | `True`, `False`, and default-when-omitted |

Per cell:

1. Create logger under `tmp_path`
2. Write a known message
3. Close handlers
4. Reopen with the appropriate reader (plain / gzip / bz2 / lzma / zstd)
5. Assert message present
6. Assert file suffix (none / `.gz` / `.bz2` / `.xz` / `.zst`) and naming (day-collapsed vs timestamped)

`zstd` cells: run when `compression.zstd` imports; otherwise skip via marker/`importorskip`.  
Spot-check that `compression_level` is accepted (no quality/ratio assertion required).

### Handlers / symlink (`test_handlers.py`)

- Two `teeLogger` instances with the same `programName` → one file handler; each distinct message appears once
- On Unix, `{programName}_latest.log` (or compressed suffix) symlink points at the active file; skip symlink asserts off-Unix

### Cleanup (`test_cleanup.py`)

Behavioral coverage on real temp dirs:

- Seed `{tmp}/{prog}_log/YYYY-MM-DD/` folders with controlled old dates
- With aggressive `deleteLogAfterYears` / `compressLogAfterMonths`, assert old dirs deleted and mid-age dirs become `.tar.xz`
- Force/mock absence of system `tar` to exercise Python `tarfile` fallback
- `compressLogAfterMonths=0` and `deleteLogAfterYears=0` disable the corresponding actions

### CLI (`test_cli.py`)

- `-V` / `--version` print version and exit 0 (subprocess against the module/`__main__` path)

### Doctests

Collect and run existing module doctests via pytest; do not keep a separate unittest wrapper.

## CI

File: `.github/workflows/test.yml`

- Triggers: `push` and `pull_request` to `master`
- Matrix: Python `3.9`, `3.11`, `3.12`, `3.14` on `ubuntu-latest`
- Steps: checkout → `actions/setup-python` → install package + dev/test deps (editable install preferred) → `pytest -q`
- zstd: real round-trips on 3.14 when available; older Pythons skip those cells; dedicated fallback-to-xz test still runs everywhere
- No coverage gate

## Error and edge expectations

- Invalid compression still produces a working xz logger
- Unwritable primary dir exercises fallback; tests must not leave the process crashed
- Maintenance disabled with `0` causes no archive/delete side effects
- Background cleanup: prefer sync helper invocation or bounded wait with a clear failure

## Documentation updates

- README Testing: primary pytest command; note CI matrix; drop unittest-discover as the main instructions
- Keep `python src/Tee_Logger.py --test` and `-V` as secondary module entry points (they remain supported); pytest (including doctests) is the authoritative suite

## Success criteria

- `pytest` (including doctests) passes locally
- CI green on Python 3.9, 3.11, 3.12, and 3.14
- Migrated caller and doctest coverage preserved
- Compression matrix cells and cleanup behaviors above are represented by real tests

## Implementation order (for planning)

1. Tooling: `pyproject.toml` dev deps + pytest ini; README testing blurb
2. `conftest.py` fixtures and log read helpers
3. Migrate helpers/caller/handlers tests; delete old unittest files
4. `test_init_config.py` and `test_logging_api.py`
5. `test_compression.py` matrix
6. `test_cleanup.py`
7. `test_cli.py`
8. GitHub Actions workflow
9. Full local + CI verification
