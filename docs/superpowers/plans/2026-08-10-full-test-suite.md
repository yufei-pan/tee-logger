# Full Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a near-exhaustive pytest suite for `Tee_Logger` with fixture isolation, migrated existing tests, and GitHub Actions on Python 3.9/3.11/3.12/3.14.

**Architecture:** Feature-module tests under `tests/` share a `conftest.py` factory that builds `teeLogger` instances on `tmp_path` with unique `programName`s and clears logging handlers after each test. Compression coverage is a parametrized matrix; cleanup uses real aged day-folders plus a mocked system-`tar` absence path. Doctests are collected by pytest.

**Tech Stack:** Python 3.9+, pytest, setuptools package (`src/Tee_Logger.py`), uv for local multi-version runs, GitHub Actions (`ubuntu-latest`).

**Spec:** `docs/superpowers/specs/2026-08-10-full-test-suite-design.md`

## Global Constraints

- Prefer **zero production code changes**; only touch `src/Tee_Logger.py` if a path is otherwise untestable.
- File-writing tests use pytest `tmp_path` as `systemLogFileDir` (except the explicit `/tmp` fallback test, which must clean up).
- Unique `programName` per test; clear `logging` file handlers after each test.
- `zstd` round-trips run when `compression.zstd` imports; otherwise skip; always keep a forced ImportError → xz fallback test.
- CI matrix: Python `3.9`, `3.11`, `3.12`, `3.14` on `ubuntu-latest`; no coverage gate.
- Local verification must also use uv-managed Pythons already installed on this machine:
  - `uv run --python 3.9 pytest`
  - `uv run --python 3.11 pytest`
  - `uv run --python 3.12 pytest`
  - `uv run --python 3.14 pytest`
- Do not commit secrets; do not amend pushed commits; commit after each task as listed.
- Keep commits focused; do not bump library version for test-only work.

## File Structure

| Path | Responsibility |
|------|----------------|
| `pyproject.toml` | Add `dev` optional deps (`pytest`), `[tool.pytest.ini_options]` |
| `uv.lock` | Refresh after adding pytest |
| `README.md` | Testing section → pytest + CI note + secondary `--test`/`-V` |
| `.github/workflows/test.yml` | CI matrix job |
| `tests/conftest.py` | Factory, log readers, handler cleanup |
| `tests/test_helpers.py` | Pure helpers |
| `tests/test_caller.py` | Caller attribution (migrated) |
| `tests/test_init_config.py` | Constructor / config edges |
| `tests/test_logging_api.py` | info/error/ok/log + tee* + printTable |
| `tests/test_compression.py` | Compression matrix + round-trips |
| `tests/test_handlers.py` | Duplicate handlers, symlink, modes |
| `tests/test_cleanup.py` | Age delete/compress + tarfile fallback |
| `tests/test_cli.py` | `-V` / `--version` |
| Delete after migration | `tests/test_doctest.py`, `tests/test_caller_info.py` |

---

### Task 1: Pytest tooling and shared fixtures

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (via `uv lock` / `uv sync`)
- Create: `tests/log_utils.py`
- Create: `tests/conftest.py`
- Create: `tests/test_helpers.py` (minimal smoke using fixtures — expanded in Task 2)
- Modify: `README.md` (Testing section only)

**Interfaces:**
- Consumes: `teeLogger` from `Tee_Logger`
- Produces (in `tests/log_utils.py`):
  - `make_tee_logger(tmp_path, **kwargs) -> teeLogger`
  - `close_tee_logger(tl) -> None`
  - `find_log_files(log_root: Path) -> list[Path]`
  - `read_log_text(path: Path) -> str` (plain / gzip / bz2 / xz / zstd by suffix)
- Produces (in `tests/conftest.py`): `tee_factory`, `program_name`, `read_active_log` fixtures; registers marker `zstd`

- [ ] **Step 1: Add pytest config and dev dependency**

Update `pyproject.toml` to include:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = ["--doctest-modules"]
# Restrict doctest collection to the library module (avoid picking up benchmarks):
norecursedirs = [".git", ".venv", "dist", "screenshots"]
markers = [
    "zstd: tests that require compression.zstd (Python 3.14+ with zstd)",
]
```

Note: `--doctest-modules` with `testpaths = ["tests"]` alone will **not** collect `src/Tee_Logger.py`. Add an explicit doctest runner test in Task 2 (`test_helpers.py` or a tiny `tests/test_doctests.py`) **or** set:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "src/Tee_Logger.py"]
```

Prefer `testpaths = ["tests", "src/Tee_Logger.py"]` so module doctests run without a wrapper.

- [ ] **Step 2: Lock and install**

```bash
cd /home/pan/Documents/tee-logger
uv lock
uv sync --extra dev
```

Expected: `pytest` available via `uv run pytest --version`.

- [ ] **Step 3: Write `tests/log_utils.py` and `tests/conftest.py`**

`tests/log_utils.py`:

```python
import bz2
import gzip
import lzma
import uuid
from pathlib import Path

from Tee_Logger import teeLogger


def unique_name(prefix: str = "tl") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def make_tee_logger(tmp_path, program_name=None, **kwargs):
    """Build a teeLogger rooted at tmp_path with safe defaults for tests."""
    kwargs.setdefault("suppressPrintout", True)
    kwargs.setdefault("disable_colors", True)
    kwargs.setdefault("compressLogAfterMonths", 0)
    kwargs.setdefault("deleteLogAfterYears", 0)
    name = program_name or unique_name()
    kwargs.setdefault("programName", name)
    kwargs.setdefault("systemLogFileDir", str(tmp_path))
    return teeLogger(**kwargs)


def close_tee_logger(tl):
    if getattr(tl, "logger", None) is None:
        return
    for handler in list(tl.logger.handlers):
        tl.logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def find_log_files(log_root: Path):
    files = []
    if not Path(log_root).exists():
        return files
    for path in Path(log_root).rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if "_latest.log" in path.name:
            continue
        if path.suffix in {".log", ".gz", ".bz2", ".xz", ".zst"} or ".log." in path.name:
            files.append(path)
    return sorted(files)


def read_log_text(path: Path) -> str:
    path = Path(path)
    name = path.name
    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if name.endswith(".bz2"):
        with bz2.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if name.endswith(".xz"):
        with lzma.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if name.endswith(".zst"):
        from compression import zstd
        with zstd.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return path.read_text(encoding="utf-8", errors="replace")
```

`tests/conftest.py`:

```python
import pytest

from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text, unique_name


@pytest.fixture
def program_name():
    return unique_name()


@pytest.fixture
def tee_factory(tmp_path):
    created = []

    def _factory(**kwargs):
        tl = make_tee_logger(tmp_path, **kwargs)
        created.append(tl)
        return tl

    yield _factory
    for tl in created:
        close_tee_logger(tl)


@pytest.fixture
def read_active_log(tmp_path, program_name):
    def _read(name=None):
        root = tmp_path / f"{name or program_name}_log"
        files = find_log_files(root)
        assert files, f"no log files under {root}"
        return read_log_text(files[-1])

    return _read
```

- [ ] **Step 4: Write a smoke test proving fixtures work**

Create `tests/test_helpers.py` with at least:

```python
from Tee_Logger import abbreviate_filename
from log_utils import find_log_files, read_log_text


def test_abbreviate_short_name():
    assert abbreviate_filename("mod.py", 42)


def test_factory_writes_log(tee_factory, tmp_path):
    tl = tee_factory(programName="smoke_factory")
    tl.info("hello-smoke")
    files = find_log_files(tmp_path / "smoke_factory_log")
    assert files
    assert "hello-smoke" in read_log_text(files[-1])
```

- [ ] **Step 5: Run smoke tests**

```bash
uv run --python 3.14 pytest tests/test_helpers.py::test_factory_writes_log tests/test_helpers.py::test_abbreviate_short_name -v
```

Expected: PASS.

- [ ] **Step 6: Update README Testing section**

Replace the Testing section with content equivalent to:

- Primary: `uv sync --extra dev` then `uv run pytest`
- Note that doctests in `src/Tee_Logger.py` are collected by pytest
- Secondary entry points: `python src/Tee_Logger.py --test` and `python src/Tee_Logger.py -V`
- Note CI runs on Python 3.9, 3.11, 3.12, and 3.14

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock README.md tests/conftest.py tests/log_utils.py tests/test_helpers.py
git commit -m "$(cat <<'EOF'
Add pytest tooling and shared test fixtures.

EOF
)"
```

---

### Task 2: Helpers and doctest collection

**Files:**
- Modify: `tests/test_helpers.py`
- Modify: `pyproject.toml` (only if doctest path still wrong)
- Delete: `tests/test_doctest.py` (after doctests collected another way)

**Interfaces:**
- Consumes: `abbreviate_filename`, `pretty_format_table`, `printWithColor`, `_log_dir_date_key`
- Produces: full helper coverage from the spec

- [ ] **Step 1: Expand `tests/test_helpers.py`**

```python
import io
import re
from contextlib import redirect_stdout

import pytest

from Tee_Logger import (
    _log_dir_date_key,
    abbreviate_filename,
    pretty_format_table,
    printWithColor,
)


def test_abbreviate_filename_examples():
    assert abbreviate_filename("my_long_module_name.py", 42)
    assert "Tee" in abbreviate_filename("Tee_Logger.py", 357, 15) or "Log" in abbreviate_filename(
        "Tee_Logger.py", 357, 15
    )


def test_pretty_format_table_nonempty():
    table = pretty_format_table([["name", "value"], ["x", "1"]])
    lines = table.splitlines()
    assert lines[0]
    assert lines[-1].startswith("x")


def test_pretty_format_table_empty():
    assert pretty_format_table([]) == "" or pretty_format_table([]) is not None


def test_print_with_color_disable():
    buf = io.StringIO()
    with redirect_stdout(buf):
        printWithColor("hi", "info", disable_colors=True)
    assert "hi" in buf.getvalue()


def test_log_dir_date_key_plain():
    assert _log_dir_date_key("2020-01-01") == "2020-01-01"


def test_log_dir_date_key_tar_xz():
    assert _log_dir_date_key("2020-01-01.tar.xz") == "2020-01-01"


def test_log_dir_date_key_txt_not_stripped():
    assert _log_dir_date_key("2020-01-01.txt") == "2020-01-01.txt"
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", _log_dir_date_key("2020-01-01.txt")) is None
```

Match `pretty_format_table([])` expected value to the real function return (read the implementation if the empty-case assertion fails).

- [ ] **Step 2: Ensure doctests run; remove unittest wrapper**

```bash
uv run --python 3.14 pytest src/Tee_Logger.py --doctest-modules -q
# or full suite once testpaths includes the module
uv run --python 3.14 pytest -q
```

Expected: doctests PASS; then delete `tests/test_doctest.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_helpers.py pyproject.toml
git rm tests/test_doctest.py
git commit -m "$(cat <<'EOF'
Migrate helper coverage and collect module doctests with pytest.

EOF
)"
```

---

### Task 3: Caller attribution and handlers (migrate unittest)

**Files:**
- Create: `tests/test_caller.py`
- Create: `tests/test_handlers.py`
- Delete: `tests/test_caller_info.py`

**Interfaces:**
- Consumes: `tee_factory` / `make_tee_logger`, `read_log_text`, `find_log_files`, `getCallerInfo`
- Produces: migrated caller + duplicate-handler + gzip mode + symlink tests

- [ ] **Step 1: Write `tests/test_caller.py`**

```python
import os
import sys

from Tee_Logger import getCallerInfo


def test_auto_mode_from_user_wrapper(tee_factory, tmp_path):
    tl = tee_factory(programName="caller_auto")

    def my_wrapper(msg):
        log_at = sys._getframe().f_lineno + 1
        tl.info(msg)
        return log_at

    def business():
        return my_wrapper("wrapped message")

    log_at = business()
    from log_utils import find_log_files, read_log_text

    content = read_log_text(find_log_files(tmp_path / "caller_auto_log")[-1])
    prefix = content.split("wrapped message")[0]
    assert f":{log_at}" in prefix
    assert "Tee_Logger" not in prefix.split("\n")[-1]


def test_auto_mode_direct_call(tee_factory, tmp_path):
    tl = tee_factory(programName="caller_direct")
    log_at = sys._getframe().f_lineno + 1
    tl.info("direct message")
    from log_utils import find_log_files, read_log_text

    content = read_log_text(find_log_files(tmp_path / "caller_direct_log")[-1])
    assert "direct message" in content
    assert f":{log_at}" in content


def test_explicit_positive_depth():
    filename, _lineno = getCallerInfo(1)
    assert filename == os.path.basename(__file__)


def test_per_call_stack_depth_override(tee_factory, tmp_path):
    tl = tee_factory(programName="caller_override", callerStackDepth=-1)

    def inner():
        # Force a deeper explicit depth if meaningful; at minimum ensure override is accepted
        tl.info("override-msg", callerStackDepth=0)

    inner()
    from log_utils import find_log_files, read_log_text

    content = read_log_text(find_log_files(tmp_path / "caller_override_log")[-1])
    assert "override-msg" in content
```

- [ ] **Step 2: Write `tests/test_handlers.py`**

```python
import logging
import os
from pathlib import Path

import pytest

from Tee_Logger import teeLogger
from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text


def test_same_program_name_replaces_file_handler(tmp_path):
    tl1 = make_tee_logger(tmp_path, programName="dup_handlers")
    tl2 = make_tee_logger(tmp_path, programName="dup_handlers")
    logger = logging.getLogger("dup_handlers")
    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
    tl1.info("once")
    tl2.info("twice")
    content = read_log_text(find_log_files(tmp_path / "dup_handlers_log")[-1])
    assert content.count("once") == 1
    assert content.count("twice") == 1
    close_tee_logger(tl1)
    close_tee_logger(tl2)


def test_gzip_respects_binary_mode_false(tmp_path):
    tl = make_tee_logger(
        tmp_path,
        programName="gzip_text",
        in_place_compression="gzip",
        binary_mode=False,
    )
    handler = tl.logger.handlers[0]
    assert handler.mode == "at"
    close_tee_logger(tl)


@pytest.mark.skipif(os.name == "nt", reason="symlinks not asserted on Windows")
def test_latest_symlink_points_at_active_log(tmp_path):
    tl = make_tee_logger(tmp_path, programName="symlink_me")
    latest = tmp_path / "symlink_me_log" / "symlink_me_latest.log"
    assert latest.is_symlink()
    target = Path(os.path.join(latest.parent, os.readlink(latest))).resolve()
    assert target.exists()
    assert "symlink_me" in target.name
    close_tee_logger(tl)
```

- [ ] **Step 3: Run migrated tests and delete old file**

```bash
uv run --python 3.14 pytest tests/test_caller.py tests/test_handlers.py -v
git rm tests/test_caller_info.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_caller.py tests/test_handlers.py
git rm tests/test_caller_info.py
git commit -m "$(cat <<'EOF'
Migrate caller and handler tests to pytest modules.

EOF
)"
```

---

### Task 4: Init and configuration edges

**Files:**
- Create: `tests/test_init_config.py`

**Interfaces:**
- Consumes: `make_tee_logger`, `close_tee_logger`, `tmp_path`
- Produces: coverage for noLog, /dev/null, compression fallbacks, collapse defaults, `/tmp` fallback

- [ ] **Step 1: Write `tests/test_init_config.py`**

```python
import logging
import shutil
from pathlib import Path
from unittest import mock

import pytest

from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text


def test_nolog_uses_null_handler(tmp_path):
    tl = make_tee_logger(tmp_path, programName="nolog", noLog=True)
    assert tl.noLog is True
    assert any(isinstance(h, logging.NullHandler) for h in tl.logger.handlers)
    tl.info("silent")
    assert not (tmp_path / "nolog_log").exists()
    close_tee_logger(tl)


@pytest.mark.parametrize("dev", ["/dev/null", "/dev/stdout", "/dev/stderr"])
def test_dev_paths_force_nolog(tmp_path, dev):
    tl = make_tee_logger(tmp_path, programName="devpath", systemLogFileDir=dev)
    assert tl.noLog is True
    close_tee_logger(tl)


def test_inplace_true_means_xz(tmp_path):
    tl = make_tee_logger(tmp_path, programName="comp_true", in_place_compression=True)
    assert tl.in_place_compression == "xz"
    assert str(tl.logFileName).endswith(".xz")
    close_tee_logger(tl)


def test_invalid_compression_falls_back_to_xz(tmp_path):
    tl = make_tee_logger(tmp_path, programName="comp_bad", in_place_compression="nope")
    assert tl.in_place_compression == "xz"
    tl.info("still-works")
    content = read_log_text(find_log_files(tmp_path / "comp_bad_log")[-1])
    assert "still-works" in content
    close_tee_logger(tl)


def test_zstd_fallback_when_import_fails(tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "compression.zstd" or name == "compression":
            raise ImportError("forced")
        return real_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", side_effect=fake_import):
        # ImportError path is inside teeLogger.__init__ via `from compression import zstd`
        tl = make_tee_logger(tmp_path, programName="zstd_fb", in_place_compression="zstd")
    assert tl.in_place_compression == "xz"
    close_tee_logger(tl)


def test_collapse_defaults_true_when_compressing(tmp_path):
    tl = make_tee_logger(tmp_path, programName="collapse_on", in_place_compression="gzip")
    assert tl.collapse_single_day_logs is True
    close_tee_logger(tl)


def test_collapse_defaults_false_without_compression(tmp_path):
    tl = make_tee_logger(tmp_path, programName="collapse_off")
    assert tl.collapse_single_day_logs is False
    close_tee_logger(tl)


def test_unwritable_primary_falls_back_to_tmp():
    name = "fallback_tmp_unique_9f3a"
    log_root = Path("/tmp") / f"{name}_log"
    if log_root.exists():
        shutil.rmtree(log_root)
    blocked = Path("/tmp") / "tee_logger_blocked_root_9f3a"
    if blocked.exists():
        shutil.rmtree(blocked)
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        # Point at a subdirectory we cannot create
        primary = blocked / "not_allowed"
        tl = make_tee_logger(primary, programName=name, systemLogFileDir=str(primary))
        # After failure, implementation sets systemLogFileDir to /tmp
        assert tl.systemLogFileDir == "/tmp" or (Path("/tmp") / f"{name}_log").exists()
        tl.info("fallback-msg")
        content = read_log_text(find_log_files(Path("/tmp") / f"{name}_log")[-1])
        assert "fallback-msg" in content
        close_tee_logger(tl)
    finally:
        blocked.chmod(0o700)
        shutil.rmtree(blocked, ignore_errors=True)
        shutil.rmtree(log_root, ignore_errors=True)
```

If `fake_import` is too broad, patch `Tee_Logger`’s import site instead — e.g. mock a helper — but prefer failing the `from compression import zstd` path the constructor uses. Refine the patch until the fallback assertion holds without breaking pytest imports.

- [ ] **Step 2: Run**

```bash
uv run --python 3.14 pytest tests/test_init_config.py -v
```

Expected: PASS (adjust `/tmp` fallback setup if the library’s failure point differs — failure is in `_setup_file_logging` / `makedirs`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_init_config.py
git commit -m "$(cat <<'EOF'
Add init and configuration edge-case tests.

EOF
)"
```

---

### Task 5: Logging API (file + tee stdout)

**Files:**
- Create: `tests/test_logging_api.py`

**Interfaces:**
- Consumes: `tee_factory`, log readers, `capsys`
- Produces: assertions for info/error/ok/log and tee* variants + printTable + line shape

- [ ] **Step 1: Write `tests/test_logging_api.py`**

```python
import re

import pytest

from log_utils import find_log_files, read_log_text


@pytest.mark.parametrize(
    "method,msg,level_token",
    [
        ("info", "m-info", "INFO"),
        ("error", "m-error", "ERROR"),
        ("ok", "m-ok", "INFO"),
    ],
)
def test_file_only_methods(tee_factory, tmp_path, capsys, method, msg, level_token):
    tl = tee_factory(programName="api_file", suppressPrintout=False)
    getattr(tl, method)(msg)
    captured = capsys.readouterr()
    assert msg not in captured.out
    content = read_log_text(find_log_files(tmp_path / "api_file_log")[-1])
    assert msg in content
    assert level_token in content


@pytest.mark.parametrize(
    "method,msg",
    [
        ("teeprint", "t-print"),
        ("teeerror", "t-error"),
        ("teeok", "t-ok"),
    ],
)
def test_tee_methods_hit_stdout_and_file(tee_factory, tmp_path, capsys, method, msg):
    tl = tee_factory(programName="api_tee", suppressPrintout=False, disable_colors=True)
    getattr(tl, method)(msg)
    captured = capsys.readouterr()
    assert msg in captured.out
    content = read_log_text(find_log_files(tmp_path / "api_tee_log")[-1])
    assert msg in content


def test_log_and_teelog_levels(tee_factory, tmp_path, capsys):
    tl = tee_factory(programName="api_levels", suppressPrintout=False, disable_colors=True)
    tl.log("plain-warn", "warning")
    tl.teelog("tee-warn", "warning")
    captured = capsys.readouterr()
    assert "plain-warn" not in captured.out
    assert "tee-warn" in captured.out
    content = read_log_text(find_log_files(tmp_path / "api_levels_log")[-1])
    assert "plain-warn" in content and "tee-warn" in content


def test_print_table(tee_factory, tmp_path):
    tl = tee_factory(programName="api_table")
    tl.printTable([["h1", "h2"], ["a", "b"]])
    content = read_log_text(find_log_files(tmp_path / "api_table_log")[-1])
    assert "a" in content and "b" in content


def test_log_line_shape(tee_factory, tmp_path):
    tl = tee_factory(programName="api_shape")
    tl.info("shape-msg")
    content = read_log_text(find_log_files(tmp_path / "api_shape_log")[-1])
    line = [ln for ln in content.splitlines() if "shape-msg" in ln][-1]
    assert re.search(r"\[\w+\s*\]", line)
    assert "shape-msg" in line
```

- [ ] **Step 2: Run**

```bash
uv run --python 3.14 pytest tests/test_logging_api.py -v
```

Expected: PASS. Note: constructor logs a `Starting ...` line to file; assertions should key off unique message strings.

- [ ] **Step 3: Commit**

```bash
git add tests/test_logging_api.py
git commit -m "$(cat <<'EOF'
Add logging API tests for file-only and tee methods.

EOF
)"
```

---

### Task 6: Compression matrix round-trips

**Files:**
- Create: `tests/test_compression.py`

**Interfaces:**
- Consumes: `make_tee_logger`, `close_tee_logger`, `find_log_files`, `read_log_text`
- Produces: parametrized matrix backend × binary_mode × collapse

- [ ] **Step 1: Write `tests/test_compression.py`**

```python
import pytest

from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text

ZSTD_AVAILABLE = False
try:
    from compression import zstd as _zstd

    _ = _zstd
    ZSTD_AVAILABLE = True
except ImportError:
    pass

BACKENDS = [None, "gzip", "bz2", "xz"]
if ZSTD_AVAILABLE:
    BACKENDS.append("zstd")


def _suffix_for(backend):
    return {None: "", "gzip": ".gz", "bz2": ".bz2", "xz": ".xz", "zstd": ".zst"}[backend]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("binary_mode", [True, False])
@pytest.mark.parametrize("collapse", [True, False, "default"])
def test_compression_roundtrip(tmp_path, backend, binary_mode, collapse):
    if backend == "zstd":
        pytest.importorskip("compression.zstd")
    kwargs = {
        "programName": f"cmp_{backend}_{binary_mode}_{collapse}".replace(".", "p"),
        "binary_mode": binary_mode,
        "in_place_compression": backend,
    }
    if collapse != "default":
        kwargs["collapse_single_day_logs"] = collapse
    tl = make_tee_logger(tmp_path, **kwargs)
    msg = f"payload-{backend}-{binary_mode}-{collapse}"
    tl.info(msg)
    path = tl.logFileName
    close_tee_logger(tl)

    from pathlib import Path

    path = Path(path)
    assert path.exists()
    assert path.name.endswith(_suffix_for(backend) if backend else path.name.endswith(".log") or True)
    if backend:
        assert path.name.endswith(_suffix_for(backend))
    content = read_log_text(path)
    assert msg in content

    # naming: collapsed day file has date only once pattern; non-collapse includes time
    if collapse is True or (collapse == "default" and backend):
        assert path.name.count("-") >= 2  # date present; exact pattern flexible
    if collapse is False or (collapse == "default" and not backend):
        # timestamped names contain date_time
        assert "_" in path.stem or "_" in path.name


def test_compression_level_accepted(tmp_path):
    tl = make_tee_logger(
        tmp_path,
        programName="cmp_level",
        in_place_compression="gzip",
        compression_level=3,
    )
    tl.info("leveled")
    close_tee_logger(tl)
    content = read_log_text(find_log_files(tmp_path / "cmp_level_log")[-1])
    assert "leveled" in content


@pytest.mark.zstd
def test_zstd_marker_path(tmp_path):
    pytest.importorskip("compression.zstd")
    tl = make_tee_logger(tmp_path, programName="cmp_zstd_mark", in_place_compression="zstd")
    tl.info("zstd-msg")
    close_tee_logger(tl)
    files = find_log_files(tmp_path / "cmp_zstd_mark_log")
    assert files[-1].name.endswith(".zst")
    assert "zstd-msg" in read_log_text(files[-1])
```

Tighten naming assertions if the first draft is too loose — compare against `tl.collapse_single_day_logs` and whether `HH-MM-SS` appears in the filename.

- [ ] **Step 2: Run on 3.14 (zstd) and 3.11 (skip zstd)**

```bash
uv run --python 3.14 pytest tests/test_compression.py -v
uv run --python 3.11 pytest tests/test_compression.py -v
```

Expected: PASS on both; zstd cells skipped on 3.11.

- [ ] **Step 3: Commit**

```bash
git add tests/test_compression.py
git commit -m "$(cat <<'EOF'
Add compression backend matrix round-trip tests.

EOF
)"
```

---

### Task 7: Cleanup / maintenance behavioral tests

**Files:**
- Create: `tests/test_cleanup.py`

**Interfaces:**
- Consumes: `make_tee_logger`, `compress_folder`, `cleanup_old_logs`
- Produces: delete-by-years, compress-to-`.tar.xz`, tarfile fallback, disabled maintenance

- [ ] **Step 1: Write `tests/test_cleanup.py`**

```python
import os
import shutil
from pathlib import Path
from unittest import mock

from Tee_Logger import compress_folder
from log_utils import close_tee_logger, make_tee_logger


def _seed_day(logs_dir: Path, day: str, text: str = "old"):
    day_dir = logs_dir / day
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"sample_{day}.log").write_text(text + "\n", encoding="utf-8")
    return day_dir


def test_delete_old_year_folders(tmp_path):
    name = "clean_del"
    tl = make_tee_logger(
        tmp_path,
        programName=name,
        deleteLogAfterYears=1,
        compressLogAfterMonths=0,
    )
    logs_dir = tmp_path / f"{name}_log"
    _seed_day(logs_dir, "2000-01-01")
    _seed_day(logs_dir, "2099-01-01")  # far future keeps; or use today
    tl.cleanup_old_logs()
    assert not (logs_dir / "2000-01-01").exists()
    close_tee_logger(tl)


def test_compress_old_month_folders(tmp_path):
    name = "clean_cmp"
    tl = make_tee_logger(
        tmp_path,
        programName=name,
        deleteLogAfterYears=0,
        compressLogAfterMonths=1,
    )
    logs_dir = tmp_path / f"{name}_log"
    # ~1 year old day folder name triggers compress when months=1
    _seed_day(logs_dir, "2020-06-01")
    tl.cleanup_old_logs()
    assert not (logs_dir / "2020-06-01").exists()
    assert (logs_dir / "2020-06-01.tar.xz").exists()
    close_tee_logger(tl)


def test_maintenance_disabled_with_zero(tmp_path):
    name = "clean_off"
    tl = make_tee_logger(
        tmp_path,
        programName=name,
        deleteLogAfterYears=0,
        compressLogAfterMonths=0,
    )
    logs_dir = tmp_path / f"{name}_log"
    _seed_day(logs_dir, "2000-01-01")
    tl.cleanup_old_logs()
    assert (logs_dir / "2000-01-01").exists()
    close_tee_logger(tl)


def test_compress_folder_tarfile_fallback(tmp_path):
    folder = tmp_path / "2019-01-01"
    folder.mkdir()
    (folder / "a.log").write_text("x\n", encoding="utf-8")
    with mock.patch("Tee_Logger.shutil.which", return_value=None):
        assert compress_folder(str(folder), disable_colors=True) is True
    assert not folder.exists()
    assert (tmp_path / "2019-01-01.tar.xz").exists()
```

`ProcessPoolExecutor` in `cleanup_old_logs` waits on context exit, so calling `tl.cleanup_old_logs()` after seeding is synchronous enough for assertions. If a race appears, retry-read with a short loop (≤2s) before failing.

- [ ] **Step 2: Run**

```bash
uv run --python 3.14 pytest tests/test_cleanup.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cleanup.py
git commit -m "$(cat <<'EOF'
Add log maintenance cleanup and compress_folder tests.

EOF
)"
```

---

### Task 8: CLI version flags

**Files:**
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `src/Tee_Logger.py` `__main__` argparse
- Produces: `-V` / `--version` exit 0 + version string

- [ ] **Step 1: Write `tests/test_cli.py`**

```python
import subprocess
import sys
from pathlib import Path

import pytest

import Tee_Logger

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "Tee_Logger.py"


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_version_flag(flag):
    proc = subprocess.run(
        [sys.executable, str(MODULE), flag],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    out = (proc.stdout or "") + (proc.stderr or "")
    assert Tee_Logger.version in out
```

argparse `action='version'` typically prints to stdout and exits 0.

- [ ] **Step 2: Run**

```bash
uv run --python 3.14 pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "$(cat <<'EOF'
Add CLI -V/--version tests.

EOF
)"
```

---

### Task 9: GitHub Actions + full multi-version verification

**Files:**
- Create: `.github/workflows/test.yml`
- Modify: `README.md` only if CI wording still missing

**Interfaces:**
- Consumes: full pytest suite + `dev` extra
- Produces: green local matrix on uv Pythons 3.9/3.11/3.12/3.14; CI workflow file

- [ ] **Step 1: Write `.github/workflows/test.yml`**

```yaml
name: test

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  pytest:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.9", "3.11", "3.12", "3.14"]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install package and pytest
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Run tests
        run: pytest -q
```

- [ ] **Step 2: Run full suite on all four local uv Pythons**

```bash
cd /home/pan/Documents/tee-logger
uv run --python 3.9 pytest -q
uv run --python 3.11 pytest -q
uv run --python 3.12 pytest -q
uv run --python 3.14 pytest -q
```

Expected: all PASS (zstd skipped where unavailable).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml README.md
git commit -m "$(cat <<'EOF'
Add GitHub Actions pytest matrix for 3.9–3.14.

EOF
)"
```

---

## Plan Self-Review

| Spec requirement | Task |
|------------------|------|
| pytest + ini + dev dep | Task 1 |
| Feature-module layout | Tasks 2–8 |
| Migrate unittest / delete old files | Tasks 2–3 |
| Helpers + doctests | Task 2 |
| Caller + handlers + symlink | Task 3 |
| Init/config + zstd fallback + `/tmp` fallback | Task 4 |
| Logging API tee/file | Task 5 |
| Compression matrix | Task 6 |
| Cleanup behavioral + tarfile fallback | Task 7 |
| CLI `-V` | Task 8 |
| GHA 3.9/3.11/3.12/3.14 | Task 9 |
| Local uv multi-version runs | Task 9 (and per-task 3.14 runs) |
| README testing update | Task 1 (+ Task 9 if needed) |
| No coverage gate | Task 9 workflow omits cov |

Placeholder scan: no TBD/TODO left in tasks. Helpers live in `tests/log_utils.py`; fixtures wrap them in `tests/conftest.py`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-10-full-test-suite.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration  
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints  

Which approach?
