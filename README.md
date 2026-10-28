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

Compressed logs are written in batches so they compress well. A batch goes to disk:

- immediately for `WARNING` and above (`warning`, `error`, `critical`, `exception`, …),
- otherwise at most `flush_interval` seconds (default 30) after a record is logged, even if the program goes quiet, or as soon as 1 MiB of records is waiting.

So if the process is killed (OOM, `SIGKILL`, segfault), at most the last `flush_interval` seconds of `DEBUG`/`INFO` records are lost; warnings and errors are already on disk. Each batch is written as a whole gzip member (xz or bz2 stream, zstd frame), and `zcat`, `xz -dc`, `bzip2 -dc`, `zstd -dc` and Python's readers read the members back as one file. So a log stays readable however its writers end, including when a restarted run or a second process with the same `programName` appends to the same day's file.

Known limitations (details in [docs/limitations.md](docs/limitations.md)):

- Each batch is compressed on its own, so frequent flushes make gzip and zstd logs larger: about +40% when one record in ten is `WARNING` or above, three times with `flush_interval=0`. Waiting records are written before each fork, which adds a member per fork. xz and bz2 are not affected.
- A batch that cannot be written (a full disk) is lost, and each failed write is reported on stderr.
- `binary_mode=False` does not apply to compressed logs.
- If the background flusher cannot be started (the process is out of threads), it is not tried again; records then reach disk on `WARNING` and above, on a later record, and at exit.
- On Python 3.6 and 3.7, when records fall back to stderr, another thread's output can land inside a record's line.
- A forked child that leaves with `os._exit` or is killed loses the records it logged since its last write; so do the workers of a `fork` Pool ended by `terminate()` (its `with` block). Workers that end normally do not.

Disable file logging:

```python
tl = teeLogger(noLog=True)
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `systemLogFileDir` | `'.'` | Root directory for log folders; on failure falls back to a private `teeLogger-<uid>/` (mode 0700) under the system temp dir |
| `programName` | caller file name without extension | Log file prefix and stdlib logger name (see below) |
| `suppressPrintout` | `not stdout.isatty()` | Hide console output from `tee*` / `printTable` and the `Log file:` banner; file-only methods never print |
| `disable_colors` | auto | Plain `[LEVEL] msg` output instead of ANSI colors; by default only when the stream is not a terminal |
| `callerStackDepth` | `-1` | Auto-resolve direct caller; use `0+` for manual stack offset |
| `in_place_compression` | `None` | `zstd`/`zst`, `xz`/`lzma`, `gzip`, `bz2`, or `True` (first available, in that order) |
| `compression_level` | backend default | gzip/bz2 `compresslevel`, xz `preset`, zstd `level` (xz presets 7–9 need hundreds of MB of RAM) |
| `flush_interval` | `30` | Max seconds before compressed records reach disk; `0` = every record, `float('inf')` = only on WARNING+, per 1 MiB of records, and at close |
| `binary_mode` | `True` | Binary append mode for uncompressed log files; compressed ones are always binary |
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

Log files are opened without following symlinks, so a link planted at a predictable log path cannot redirect writes into another file. Still, avoid pointing `systemLogFileDir` (or running with the default `.`) at a directory other users can write to.

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

Folders with a file modified in the last 24 hours are left alone, in case another process is still writing there. A folder named like a date that is not one (`2020-13-45`) is skipped, with a message on stderr. The work runs in a background thread; the program exits only after an archive in progress is finished. `cleanup_old_logs()` runs the same maintenance synchronously.

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

## Upgrading to 6.44 from 6.43

- Compressed logs are written as one whole gzip member (xz or bz2 stream, zstd frame) per flush. In 6.43 a gzip or zstd log could not be read past the point where a run was killed, once a restarted run or a second process with the same `programName` appended to the same day's file; such a file now stays readable. The cost: gzip and zstd logs with frequent `WARNING`+ records, or with `flush_interval=0`, are larger (see the limitations above). xz and bz2 logs are unchanged.
- A compressed batch that cannot be written (a full disk) is dropped and reported each time it fails; a partial write is cut back so later records stay readable.
- `binary_mode=False` no longer affects compressed logs.
- `teeLogger.GZipFileHandler`, `BZ2FileHandler`, `XZFileHandler` and `ZSTDFileHandler` are gone; one internal handler writes all four formats. `teeLogger.BinFileHandler` stays.
- `flush_interval` is taken with `float()`: `'30'` now means 30 seconds (6.43 raised `TypeError`), and NaN means "only on `WARNING` and above and at close" (6.43 raised `ValueError`).
- Console lines go through `print()` again, still with each whole line in one write, so a program that replaces `builtins.print` sees them.
- At midnight the log handler switches to the new day's file itself. A signal handler that logs while its thread holds `logging`'s own lock can no longer deadlock with another thread's midnight switch.
- A `logging.FileHandler` you attach yourself to the logger named `programName` is no longer closed when another `teeLogger` of that name is made.
- If the background flusher cannot be started, it is not tried again (6.43 retried once per interval).
- Compressed records still waiting when the process forks are written before the fork (Python 3.7+), so neither process writes them twice or drops them (a daemon's parent leaving with `os._exit`), and a child that logs starts a flush timer of its own. A multiprocessing worker writes its last records when it ends. 6.43 duplicated such records, or corrupted gzip and zstd logs, and lost a worker's last ones. A program that forks often while records wait gets one more compressed member per fork. A child forked while another thread was switching to the new day's file no longer hangs at its own next midnight.
- The `tee*` methods log to the file before they print, so a console that fails (`prog | head -1`) no longer loses the record from the file; the error is still raised.
- A compression level the backend refuses is reported by name (`gzip refuses compression_level 10 (...)`).
- A day-folder named like a date that is not one (`2020-13-45`) is skipped with a message, instead of being aged by its mtime.
- The stderr fallback uses the stdlib `StreamHandler`; see the 3.6/3.7 limitation above.

## Upgrading to 6.43 from 6.42

- When a compressed log file will use it (`in_place_compression` set, `noLog` off, and a log directory other than `/dev/null`, `/dev/stdout` or `/dev/stderr`), `flush_interval` must be a number: NaN raises `ValueError` and a non-number (`None`, `'30'`) raises `TypeError` when the logger is made, and the value is kept as a float. 6.42 accepted them: `None` and `'30'` then printed a logging error for every record below WARNING, and NaN kept its flusher thread busy on a CPU core while records reached disk only on WARNING and above and at close. Otherwise the value is still not checked.
- `flush_interval=float('inf')` no longer prints a traceback from the flusher thread; compressed logs then reach disk on WARNING and above and at close, as in 6.42.
- Console and diagnostic lines are written with one `write()` each instead of through `print()`, so lines from several threads no longer interleave, and a program that replaces `builtins.print` no longer changes tee-logger's output.
- Fixed: a failed background flush is reported once instead of stopping the flusher for good (gzip and zstd then retry it; with xz and bz2 the records of the stream that failed are still lost); a failed close of the old log at midnight, or a stderr that cannot be written, no longer makes a log call raise or leaves the logger without a log file; records other threads log during the midnight switch are no longer lost; a signal handler that logs while its own thread is rolling the log over at midnight no longer deadlocks the process.

## Upgrading to 6.42 from 6.40 or earlier

- The default `programName` drops the file extension: `myscript.py` now logs to `myscript_log/` instead of `myscript.py_log/`. Pass `programName='myscript.py'` to keep the old folder, or move it.
- `in_place_compression=True` picks zstd on Python 3.14+ (previously always xz), so the file suffix may change.
- `compression_level` now takes effect; it was silently ignored before.
- The `Log file:` banner and other internal messages moved from stdout to stderr.
- `python-dateutil` is no longer a dependency.
- When the log directory is not writable, logs fall back to `$TMPDIR/teeLogger-<uid>/` instead of `/tmp` directly.

## License

GPL-3.0-or-later
