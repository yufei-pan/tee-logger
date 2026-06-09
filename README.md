# Tee_Logger

A Python logger that writes to a dated log file and optionally mirrors output to stdout with ANSI colors. Supports in-place compression, automatic log archival, and smart caller line attribution.

## Features

- **Tee logging** — file-only (`info`, `error`, …) or stdout+file (`teeprint`, `teeerror`, `teeok`, …)
- **Dated layout** — `{programName}_log/YYYY-MM-DD/{programName}_YYYY-MM-DD_HH-MM-SS.log`
- **Caller attribution** — log lines include abbreviated `file:line` of the direct caller
- **Compression** — write `.gz`/`.bz2`/`.xz`/`.zst` logs directly, or tar.xz archive old day-folders
- **Maintenance** — auto-compress and delete logs by age on startup

## Installation

```bash
pip install tee-logger
```

Optional runtime dependency: `python-dateutil` (improves log-folder date parsing during cleanup).

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
    in_place_compression='zstd',  # or 'gzip', 'bz2', 'xz'
    compression_level=3,
)
```

Disable file logging:

```python
tl = teeLogger(noLog=True)
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `systemLogFileDir` | `'.'` | Root directory for log folders; falls back to `/tmp` on failure |
| `programName` | caller file name | Logger name and log file prefix |
| `suppressPrintout` | `not stdout.isatty()` | Hide console output from `tee*` methods |
| `callerStackDepth` | `-1` | Auto-resolve direct caller; use `0+` for manual stack offset |
| `in_place_compression` | `None` | `gzip`, `bz2`, `xz`/`lzma`, `zstd`, or `True` (xz) |
| `binary_mode` | `True` | Binary append mode for log files |
| `compressLogAfterMonths` | `2` | Archive day-folders older than N months (`0` = off) |
| `deleteLogAfterYears` | `2` | Delete day-folders older than N years (`0` = off) |
| `noLog` | `False` | Disable file logging |
| `fileDescriptorLength` | `15` | Width of `file:line` in log records |
| `encoding` | `'utf-8'` | Log file encoding |
| `collapse_single_day_logs` | auto | One file per day when compressing in-place |

Per-call override: `tl.info('msg', callerStackDepth=3)`.

## Log layout

```
MyApp_log/
├── MyApp_latest.log          # symlink to active log (Unix)
└── 2025-02-10/
    └── MyApp_2025-02-10_01-33-26.log
```

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

On initialization, `cleanup_old_logs()` scans `{programName}_log/` for `YYYY-MM-DD` folders:

1. **Delete** folders older than `deleteLogAfterYears`
2. **Compress** folders older than `compressLogAfterMonths` to `.tar.xz`

Compression uses system `tar`+`xz` when available, otherwise Python's `tarfile`. Work runs in a background process only when there are eligible folders.

## API reference

Full docstrings and examples live in the module:

```bash
python -c "import Tee_Logger; help(Tee_Logger.teeLogger)"
```

Public helpers: `abbreviate_filename`, `pretty_format_table`, `printWithColor`, `getCallerInfo`, `teeLogger`.

## Testing

Run doctests embedded in the module:

```bash
python -m doctest src/Tee_Logger.py -v
python src/Tee_Logger.py --test
```

Run unit tests:

```bash
python -m unittest discover -s tests -v
```

## License

GPL-3.0-or-later
