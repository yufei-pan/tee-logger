# Tee_Logger

A Python logger that writes to a dated log file and optionally mirrors output to stdout with ANSI colors. Supports in-place compression, automatic log archival, and smart caller line attribution.

## Features

- **Tee logging** — file-only (`debug`, `info`, `warning`, `error`, `critical`, `exception`, …) or stdout+file (`teeprint`, `teewarning`, `teeerror`, `teeexception`, …)
- **Dated layout** — `{programName}_log/YYYY-MM-DD/{programName}_YYYY-MM-DD_HH-MM-SS.log`, rolling over to a new day at midnight
- **Caller attribution** — log lines include abbreviated `file:line` of the direct caller
- **Compression** — write `.zst`/`.xz`/`.gz`/`.bz2` logs directly (crash-safe, see below), or tar.xz archive old day-folders
- **Maintenance** — auto-compress and delete logs by age, in a background thread

## Installation

```bash
pip install tee-logger
```

No runtime dependencies; `src/Tee_Logger.py` also works when copied on its own.

Requires Python 3.6+. In-place `zstd` compression requires Python 3.14+ with zstd support in the build; otherwise it falls back to `xz`.

## Quick start

```python
from Tee_Logger import teeLogger

tl = teeLogger(systemLogFileDir='.', programName='MyApp')
tl.info('file only')
tl.teeprint('stdout and file')
tl.teeerror('error to both')
```

Compressed logs:

```python
tl = teeLogger(
    programName='MyApp',
    in_place_compression=True,  # first available of zstd, xz, gzip, bz2; or name one
    compression_level=3,        # optional, backend-specific
)
```

Compressed logs are written in batches so they compress well, and flushed to disk:

- immediately for `WARNING` and above (`warning`, `error`, `critical`, `exception`, …),
- otherwise at most `flush_interval` seconds (default 30) after a record is written, even if the program goes quiet.

So if the process is killed (OOM, `SIGKILL`, segfault), at most the last `flush_interval` seconds of `DEBUG`/`INFO` records are lost; warnings and errors are already on disk. gzip and zstd flush mid-stream; xz and bz2 cannot, so each flush finishes the current stream and starts a new one in the same file (`xz -dc`, `bzip2 -dc`, `lzma.open`, `bz2.open` read them all). A file that is still being written may end in an unfinished stream: `zcat`/`zstd -dc` print everything and then warn about it. `flush_interval=0` flushes every record, which costs a lot of compression (gzip/zstd files about twice as large).

Disable file logging:

```python
tl = teeLogger(noLog=True)
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `systemLogFileDir` | `'.'` | Root directory for log folders; falls back to the system temp dir (`$TMPDIR`, usually `/tmp`) on failure |
| `programName` | caller file name without extension | Log file prefix and stdlib logger name (see below) |
| `suppressPrintout` | `not stdout.isatty()` | Hide console output from `tee*` / `printTable` and the `Log file:` banner; file-only methods never print |
| `disable_colors` | auto | Plain `[LEVEL] msg` output instead of ANSI colors; by default only when the stream is not a terminal |
| `callerStackDepth` | `-1` | Auto-resolve direct caller; use `0+` for manual stack offset |
| `in_place_compression` | `None` | `zstd`/`zst`, `xz`/`lzma`, `gzip`, `bz2`, or `True` (first available, in that order) |
| `compression_level` | backend default | gzip/bz2 `compresslevel`, xz `preset`, zstd `level` (xz presets 7–9 need hundreds of MB of RAM) |
| `flush_interval` | `30` | Max seconds before compressed records reach disk; `0` = every record |
| `binary_mode` | `True` | Binary append mode for log files |
| `compressLogAfterMonths` | `2` | Archive day-folders older than N months (`0` = off) |
| `deleteLogAfterYears` | `2` | Delete day-folders older than N years (`0` = off) |
| `noLog` | `False` | Disable file logging |
| `fileDescriptorLength` | `15` | Width of `file:line` in log records |
| `encoding` | `'utf-8'` | Log file encoding |
| `collapse_single_day_logs` | auto | One file per day when compressing in-place |

Per-call override: `tl.info('msg', callerStackDepth=3)`.

### Shared logger name

`programName` is passed to `logging.getLogger(programName)`. A second `teeLogger` with the same name reuses that logger and **replaces** the previous file handler so only one active log file is attached. That is intentional (one live log per name). Use distinct `programName` values if two components need independent log files at the same time.

## Log layout

```
MyApp_log/
├── MyApp_latest.log          # symlink to active log (Unix); .zst/.xz/.gz/.bz2 suffix when compressed
├── 2024-11-30.tar.xz         # archived day-folder
└── 2025-02-10/
    └── MyApp_2025-02-10_01-33-26.log
```

A process that runs past midnight closes its file and continues in the new day's folder; the first line there names the file it continued from.

The logger's own messages (the `Log file: …` banner, fallbacks, cleanup failures) go to **stderr**, so they never mix into a tool's piped stdout. The banner is hidden when `suppressPrintout` is on.

Log line format:

```log
2025-02-10 01:33:26,623 [INFO    ] [MyApp:42      ] message text
```

## Caller stack depth

With the default `callerStackDepth=-1`, `teeLogger` skips its own frames and records the **direct caller** — your code or your wrapper, not internal `Tee_Logger` methods.

```python
def my_wrapper(msg):
    tl.info(msg)          # log shows this line

def business():
    my_wrapper('hello')   # log shows my_wrapper, not business
```

Use a non-negative `callerStackDepth` or a per-call override for advanced tuning.

## Log maintenance

On initialization and at each midnight rollover, `{programName}_log/` is scanned for `YYYY-MM-DD` folders (age comes from the folder name):

1. **Delete** folders and archives older than `deleteLogAfterYears`
2. **Compress** folders older than `compressLogAfterMonths` to `.tar.xz`

Folders with a file modified in the last 24 hours are left alone, in case another process is still writing there. The work runs in a background thread; the program exits only after an archive in progress is finished. `cleanup_old_logs()` runs the same maintenance synchronously.

Compression uses system `tar`+`xz` when available, otherwise Python's `tarfile`. It is safe against crashes and concurrent cleanups: the folder is first renamed to `YYYY-MM-DD.compressing-<pid>/`, the archive is written to a temporary file and renamed into place, and only then is the folder deleted. If archiving fails, the folder is put back; a claim left by a crashed process is picked up again after 24 hours.

## API reference

Full docstrings and examples live in the module:

```bash
python -c "import Tee_Logger; help(Tee_Logger.teeLogger)"
```

Logging methods (all accept `callerStackDepth=`):

| File only | File + console |
|-----------|----------------|
| `debug` | `teedebug` |
| `info`, `ok` | `teeprint`, `teeok` |
| `warning` | `teewarning` |
| `error` | `teeerror` |
| `critical` | `teecritical` |
| `exception` (adds traceback) | `teeexception` (traceback in file, one-line summary on console) |
| `log(msg, level)` | `teelog(msg, level)`, `printTable(data)` |

Public helpers: `abbreviate_filename`, `pretty_format_table`, `printWithColor`, `getCallerInfo`, `compress_folder`, `teeLogger`.

## Testing

Primary entry point (pytest, including doctests in `src/Tee_Logger.py`):

```bash
uv sync --extra dev
uv run pytest
```

Secondary entry points:

```bash
python src/Tee_Logger.py --test
python src/Tee_Logger.py -V
```

CI runs on Python 3.9, 3.11, 3.12, and 3.14, plus 3.6 in a `python:3.6` container.

## Upgrading from 6.40 or earlier

- The default `programName` drops the file extension: `myscript.py` now logs to `myscript_log/` instead of `myscript.py_log/`. Pass `programName='myscript.py'` to keep the old folder, or move it.
- `in_place_compression=True` picks zstd on Python 3.14+ (previously always xz), so the file suffix may change.
- `compression_level` now takes effect; it was silently ignored before.
- The `Log file:` banner and other internal messages moved from stdout to stderr.
- `python-dateutil` is no longer a dependency.

## License

GPL-3.0-or-later
