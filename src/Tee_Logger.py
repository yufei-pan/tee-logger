#!/usr/bin/env python3
"""Tee output to stdout and a dated log file with optional compression.

``teeLogger`` wraps Python's ``logging`` module to write timestamped log files
under ``{programName}_log/YYYY-MM-DD/``, optionally mirror messages to the
console with ANSI colors, and maintain old logs via compression and deletion.

Quick start::

    from Tee_Logger import teeLogger

    tl = teeLogger(systemLogFileDir='.', programName='MyApp')
    tl.info('file only')
    tl.teeprint('stdout and file')

Run doctests::

    python -m doctest src/Tee_Logger.py -v
    python src/Tee_Logger.py --test
    python src/Tee_Logger.py -V

See Also:
    README.md for installation, log layout, and maintenance policy.
"""
import datetime
import os
import logging
import inspect
import re
import base64
import math
import functools
import shutil
import stat
import tarfile
import subprocess
import sys
import tempfile
import threading
import time

version = '6.42'
__version__ = version

__author__ = 'Yufei Pan (pan@zopyr.us)'

_TEE_LOGGER_FILE = os.path.abspath(__file__)


class bcolors:
    """ANSI escape codes for colored terminal output."""

    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    warning = '\033[93m'
    critical = '\033[91m'
    error = '\033[91m'
    info = '\033[0m'
    debug = '\033[0m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

_LEVEL_COLORS = {
    'info': bcolors.info,
    'debug': bcolors.debug,
    'warning': bcolors.warning,
    'error': bcolors.error,
    'critical': bcolors.critical,
    'ok': bcolors.OKGREEN,
    'okgreen': bcolors.OKGREEN,
    'okblue': bcolors.OKBLUE,
    'okcyan': bcolors.OKCYAN,
}

@functools.lru_cache(maxsize=128)
def abbreviate_filename(filename,lineNumber, target_length=15):
    """Return a fixed-width ``filename:line`` label for log records.

    Long module names and line numbers are shortened to fit ``target_length``
    characters using camel-case abbreviation, hex, base64, or scientific
    notation as needed.

    Args:
        filename: Source file name (extension is stripped).
        lineNumber: Line number in that file.
        target_length: Total width of the returned label.

    Returns:
        A left-padded string of length ``target_length``.

    Examples:
        >>> abbreviate_filename('my_long_module_name.py', 42)
        'MyLongMN:42    '
        >>> abbreviate_filename('Tee_Logger.py', 357, 15)
        'Tee_Logger:357 '
    """
    # Remove all extensions from the filename
    filename = re.sub(r'\.[^.]*$', '', filename)
    def abbreviate_last_word(name):
        # Split the filename into parts by delimiters and camel case
        parts = re.split(r'([A-Z][a-z]*|[_\- ])', name)
        # Filter out empty parts and captialize the first letter of each part
        parts = [part.capitalize()  for part in parts if part and part not in '_- ']
        # Find the last word that can still be shortened; numbers are kept since they
        # usually tell sibling scripts apart (worker_1 / worker_2)
        for i in range(len(parts) - 1, -1, -1):
            if len(parts[i]) > 1 and parts[i].isalnum() and not parts[i].isupper() and not parts[i].isdigit():
                parts[i] = parts[i][0].upper()
                return ''.join(parts)
        # If no word can be shortened, we remove the last part (unless it is the only one)
        if len(parts) > 1:
            return ''.join(parts[:-1])
        return name
    def shorten_line_number(lineNumber,target_length=5):
        if len(str(lineNumber)) <= target_length:
            return str(lineNumber)
        # we will try to use hex number to represent the line number first
        newLineNumber = hex(lineNumber)
        if len(newLineNumber) <= target_length:
            return newLineNumber
        newLineNumber = newLineNumber[1:] # remove the 0
        if len(newLineNumber) <= target_length:
            return newLineNumber
        newLineNumber = int_to_base64(lineNumber)
        if len(newLineNumber) <= target_length:
            return newLineNumber
        # if all else fails, we will use scientific notation
        # find the exponent
        exp = math.floor(math.log10(lineNumber))
        # produce the scientific notation with highest precision available for the target length
        availble_mantissa_length = target_length - 4 - len(str(exp))
        if availble_mantissa_length >= 0:
            return f'{lineNumber:.{availble_mantissa_length}e}'
        else:
            # return the exponent with the maximum availble length
            return f'e{exp}'[:target_length]
    def int_to_base64(n):
        # Determine the number of bytes needed to represent the integer
        num_bytes = (n.bit_length() + 7) // 8
        # Convert the integer to bytes
        byte_array = n.to_bytes(num_bytes, byteorder='big')
        # Encode the bytes using base64
        b64_encoded = base64.b64encode(byte_array)
        # Convert the base64 bytes to a string and return it
        return b64_encoded.decode('ascii')
    # if the lineNumber length is greater than half of the max_length, we shorten the line number as well
    lineNumberStr = shorten_line_number(int(lineNumber), target_length=target_length//2)
    fileNameMaxLen = target_length - len(lineNumberStr) - 1
    # Continue abbreviating until the filename is under the max length
    while len(filename) > fileNameMaxLen:
        abbreviated = abbreviate_last_word(filename)
        if len(abbreviated) >= len(filename):
            # no further progress possible, fall back to truncation
            break
        filename = abbreviated
    # Truncate the filename if it is still too long
    filename = filename[:fileNameMaxLen]
    lineNumberStr = shorten_line_number(int(lineNumber), target_length=target_length - len(filename) -1)
    strOut = f"{filename}:{lineNumberStr}".ljust(target_length)
    return strOut

def _stream_is_tty(stream):
    try:
        return stream.isatty()
    except Exception:
        return False

def printWithColor(msg, level = 'info',disable_colors=..., file=None):
    """Print ``msg`` to ``file`` (default stdout) using ANSI colors for ``level``.

    When colors are disabled, output is plain text prefixed with ``[LEVEL]``.

    Args:
        msg: Text to print.
        level: Color key (``info``, ``debug``, ``warning``, ``error``,
            ``critical``, ``ok``/``okgreen``, ``okblue``, ``okcyan``).
        disable_colors: If True, omit ANSI escape sequences. The default
            (``...``) disables colors when ``file`` is not a terminal.
        file: Stream to write to. Defaults to the current ``sys.stdout``.

    Examples:
        >>> import io, contextlib
        >>> buf = io.StringIO()
        >>> with contextlib.redirect_stdout(buf):
        ...     printWithColor('hello', 'warning', disable_colors=True)
        >>> buf.getvalue()
        '[WARNING] hello\\n'
    """
    if file is None:
        file = sys.stdout
        if file is None:  # no console (pythonw); print() would do nothing either
            return
    if disable_colors is ...:
        disable_colors = not _stream_is_tty(file)
    if disable_colors:
        line = f'[{level.upper()}] {msg}\n'
    else:
        line = f'{_LEVEL_COLORS.get(level, bcolors.info)}{msg}{bcolors.ENDC}\n'
    # One write() per line, not print(), which writes the text and the newline
    # separately: on an unbuffered stream another thread's line could land in
    # between. No lock either: a signal handler that logs runs on the main thread,
    # possibly while it is in here, and would deadlock on one.
    file.write(line)

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')

def _union_keys(dicts):
    """Return the keys of all ``dicts`` in first-seen order."""
    keys = []
    seen = set()
    for item in dicts:
        for key in item:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys

def pretty_format_table(data, delimiter = '\t',header = None):
    """Format rows as an aligned text table.

    ``data`` may be a list of rows, a list of dicts, a dict of lists, a nested
    dict, or a delimiter-separated string. When ``header`` is omitted, the first
    row of ``data`` is treated as the header.

    Args:
        data: Table contents in a supported shape.
        delimiter: Field delimiter when ``data`` is a string.
        header: Optional header row (string or list of column names).

    Returns:
        Formatted table string with trailing newline, or ``''`` if ``data`` is empty.

    Examples:
        >>> table = pretty_format_table([['name', 'value'], ['x', '1']])
        >>> table.splitlines()[0]
        'name | value'
        >>> table.splitlines()[-1].startswith('x')
        True
        >>> pretty_format_table([])
        ''
    """
    if not data:
        return ''
    if isinstance(data, str):
        data = data.strip('\n').split('\n')
        data = [line.split(delimiter) for line in data]
    elif isinstance(data, dict):
        first = next(iter(data.values()))
        if isinstance(first, dict):
            # flatten the 2D dict to a list of lists, aligning values by key
            keys = _union_keys(data.values())
            data = [['key'] + keys] + [[key] + [value.get(k, '') for k in keys] for key, value in data.items()]
        else:
            # it is a dict of lists
            data = [[key] + list(value) for key, value in data.items()]
    elif not isinstance(data, list):
        data = list(data)
    # format the list into 2d list of list of strings
    if isinstance(data[0], dict):
        keys = _union_keys(data)
        data = [keys] + [[item.get(k, '') for k in keys] for item in data]
    data = [[str(item) for item in row] for row in data]
    if header:
        if isinstance(header, str):
            header = header.split(delimiter)
        header = [str(col) for col in header]
        rows = data
    else:
        header = data[0]
        rows = data[1:]
    # pad the header and every row to the widest of them
    num_cols = max([len(header)] + [len(row) for row in rows])
    header = header + [''] * (num_cols - len(header))
    rows = [row + [''] * (num_cols - len(row)) for row in rows]
    # Calculate the maximum width of each column, ignoring ansi escape sequences
    col_widths = [
        max(len(_ANSI_ESCAPE_RE.sub('', row[c])) for row in [header] + rows)
        for c in range(num_cols)
    ]
    # Build the row format string
    row_format = ' | '.join('{{:<{}}}'.format(width) for width in col_widths)
    divider = '-+-'.join('-' * width for width in col_widths)
    outTable = [row_format.format(*header), divider]
    for row in rows:
        # if the row is empty, print an divider
        if not any(row):
            outTable.append(divider)
        else:
            outTable.append(row_format.format(*row))
    return '\n'.join(outTable) + '\n'


def _is_tee_logger_frame(frame):
    try:
        return os.path.abspath(frame.f_code.co_filename) == _TEE_LOGGER_FILE
    except Exception:
        return False

def getCallerInfo(i=-1):
    """Return ``(filename, lineno)`` for a stack frame.

    When ``i`` is negative (default ``-1``), skip frames inside this module
    and return the first external caller — typically user code or a user
    wrapper that invoked a ``teeLogger`` method. Non-negative ``i`` selects an
    explicit frame offset from ``getCallerInfo`` itself (advanced use).

    Args:
        i: Stack index, or ``-1`` for automatic caller resolution.

    Returns:
        Tuple of base file name and line number. On failure, ``('TLError ...', 0)``.
    """
    frame = None
    try:
        if i < 0:
            frame = inspect.currentframe()
            if frame is not None:
                frame = frame.f_back
            while frame and _is_tee_logger_frame(frame):
                frame = frame.f_back
        else:
            frame = inspect.currentframe()
            for _ in range(i):
                if frame is None or frame.f_back is None:
                    break
                frame = frame.f_back
        if frame is None:
            return 'unknown', 0
        filename = os.path.basename(frame.f_code.co_filename)
        lineno = frame.f_lineno
    except Exception as e:
        filename = f'TLError {e}'
        lineno = 0
    finally:
        if frame is not None:
            del frame
    return filename, lineno

# A log folder or archive is "in progress" for this long before another process may take it over
_STALE_SECONDS = 24 * 3600
# Entries cleanup manages under {programName}_log/: day-folders, their archives, and the
# claims compress_folder leaves while it works (see compress_folder)
_LOG_DIR_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})(\.tar\.xz|\.compressing-\d+|\.deleting-\d+)?$')

def _log_dir_date_key(dirName):
    """Extract ``YYYY-MM-DD`` key from a log directory name.

    Examples:
        >>> _log_dir_date_key('2020-01-01')
        '2020-01-01'
        >>> _log_dir_date_key('2020-01-01.tar.xz')
        '2020-01-01'
        >>> _log_dir_date_key('2020-01-01.compressing-4242')
        '2020-01-01'
        >>> _log_dir_date_key('2020-01-01.txt')
        '2020-01-01.txt'
    """
    match = _LOG_DIR_RE.match(dirName)
    return match.group(1) if match else dirName

def _next_midnight(now):
    """Return the timestamp of the first local midnight after ``now``."""
    tomorrow = datetime.date.fromtimestamp(now) + datetime.timedelta(days=1)
    return time.mktime(tomorrow.timetuple())

def _day_timestamp(day, path):
    """Return the local-midnight timestamp of ``day`` (``YYYY-MM-DD``)."""
    try:
        return datetime.datetime.strptime(day, '%Y-%m-%d').timestamp()
    except ValueError:
        # matches the pattern but is not a real date (e.g. 2020-13-45)
        return max(os.path.getmtime(path), os.path.getctime(path))

def _recently_written(path, now):
    """Return True if any file under ``path`` was modified within the last day."""
    if not os.path.isdir(path):
        return now - os.path.getmtime(path) < _STALE_SECONDS
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                if now - os.path.getmtime(os.path.join(root, name)) < _STALE_SECONDS:
                    return True
            except OSError:
                pass
    return False

def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass

def _remove_path(path):
    """Delete a file or directory tree; a path that is already gone is fine."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except FileNotFoundError:
        pass

def _tar_with_cli(workDir, day, tmpArchive, disable_colors=...):
    """Archive ``workDir/day`` into ``tmpArchive`` with the system ``tar`` and ``xz``."""
    if os.name == 'nt' or not (shutil.which('tar') and shutil.which('xz')):
        return False
    try:
        proc = subprocess.run(['tar', '-cJf', tmpArchive, day], cwd=workDir,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            return True
        reason = proc.stderr.decode(errors='replace').strip() or f'exit code {proc.returncode}'
    except Exception as e:
        reason = str(e)
    _remove_quietly(tmpArchive)
    printWithColor(f'tar failed to compress {day} ({reason}), falling back to python implementation',
                   'warning', disable_colors=disable_colors, file=sys.stderr)
    return False

def _tar_with_tarfile(folder, day, tmpArchive, disable_colors=...):
    """Archive ``folder`` as ``day/`` into ``tmpArchive`` with :mod:`tarfile`."""
    try:
        with tarfile.open(tmpArchive, 'w:xz') as tar:
            tar.add(folder, arcname=day)
        return True
    except Exception as e:
        _remove_quietly(tmpArchive)
        printWithColor(f'Failed to compress folder {day} due to {e}', 'error',
                       disable_colors=disable_colors, file=sys.stderr)
        return False

def compress_folder(folderPath, disable_colors=...):
    """Archive a log day-folder to ``YYYY-MM-DD.tar.xz`` next to it and remove the folder.

    The folder is first claimed by moving it into ``YYYY-MM-DD.compressing-<pid>/``.
    That rename is atomic, so two processes cleaning up at once never archive the
    same folder. The archive is written to a temporary file inside the claim and
    renamed into place only once complete; only then is the claim removed (via a
    ``YYYY-MM-DD.deleting-<pid>`` rename). If anything fails, the folder is moved
    back. ``folderPath`` may also be a claim a crashed process left behind.

    Uses ``tar`` and ``xz`` when available on the PATH; otherwise falls back to
    Python's ``tarfile`` module.

    Args:
        folderPath: Path to the directory to archive.
        disable_colors: Passed through to status messages on failure.

    Returns:
        True if the archive was written, False otherwise.
    """
    folderPath = os.path.abspath(folderPath)
    parent, name = os.path.split(folderPath)
    day = _log_dir_date_key(name)
    claim = os.path.join(parent, f'{day}.compressing-{os.getpid()}')
    claimedFolder = os.path.join(claim, day)
    try:
        if name == day:
            os.mkdir(claim)
            try:
                os.rename(folderPath, claimedFolder)
            except OSError:
                os.rmdir(claim)
                return False  # already gone, or another process claimed it first
        elif folderPath != claim:
            os.rename(folderPath, claim)  # take over a claim left by a crashed process
    except OSError:
        return False
    archive = os.path.join(parent, day + '.tar.xz')
    tmpArchive = os.path.join(claim, day + '.tar.xz.tmp')
    try:
        if os.path.isdir(claimedFolder):
            if not (_tar_with_cli(claim, day, tmpArchive, disable_colors)
                    or _tar_with_tarfile(claimedFolder, day, tmpArchive, disable_colors)):
                # put the folder back so a later run can retry
                os.rename(claimedFolder, os.path.join(parent, day))
                shutil.rmtree(claim, ignore_errors=True)
                return False
            os.replace(tmpArchive, archive)
        elif not os.path.exists(archive):
            shutil.rmtree(claim, ignore_errors=True)
            return False
        deleting = os.path.join(parent, f'{day}.deleting-{os.getpid()}')
        os.rename(claim, deleting)
        shutil.rmtree(deleting, ignore_errors=True)
        return True
    except OSError as e:
        printWithColor(f'Failed to compress folder {day} due to {e}', 'error',
                       disable_colors=disable_colors, file=sys.stderr)
        return False

def _open_append_nofollow(path, mode='ab', encoding=None):
    """Open ``path`` for appending, refusing to follow a symlink or open a non-regular file.

    Log paths are predictable, so in a directory other users can write to, a
    planted symlink would otherwise redirect our writes into any file we can write.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    # O_NONBLOCK so a planted FIFO fails instead of blocking forever
    nonblock = getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags | nonblock, 0o666)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f'{path} is not a regular file')
        if nonblock:
            import fcntl
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~nonblock)
        if 'b' in mode:
            return os.fdopen(fd, 'ab')
        return os.fdopen(fd, 'a', encoding=encoding)
    except BaseException:
        os.close(fd)
        raise

class _OwningStream:
    """A compressed stream written over ``raw``; closing it also closes ``raw``."""

    def __init__(self, stream, raw):
        self._stream = stream
        self._raw = raw

    def write(self, data):
        return self._stream.write(data)

    def flush(self):
        self._stream.flush()
        self._raw.flush()

    def close(self):
        try:
            self._stream.close()
        finally:
            self._raw.close()

def _private_fallback_dir(disable_colors=...):
    """Return a directory under the system temp dir that only this user can write, or None.

    The shared temp dir is writable by everyone, so logging there directly would let
    other users plant symlinks or files at our predictable log paths.
    """
    try:
        user = str(os.getuid())
    except AttributeError:  # Windows
        import getpass
        user = getpass.getuser()
    path = os.path.join(tempfile.gettempdir(), 'teeLogger-' + user)
    try:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            problem = 'is not a directory'
        elif hasattr(os, 'getuid') and st.st_uid != os.getuid():
            problem = 'is owned by another user'
        else:
            if os.name != 'nt' and stat.S_IMODE(st.st_mode) & 0o077:
                os.chmod(path, 0o700)
            return path
    except OSError as e:
        problem = f'cannot be used ({e})'
    printWithColor(f'Refusing to log to {path}: it {problem}', 'error',
                   disable_colors=disable_colors, file=sys.stderr)
    return None

def _format_for_stream(handler, record):
    """Format ``record`` as one line for ``handler.stream`` (bytes in binary mode)."""
    msg = handler.format(record)
    if 'b' in handler.mode:
        if not isinstance(msg, bytes):
            if not isinstance(msg, str):
                msg = str(msg)
            msg = msg.encode(handler.encoding, errors='namereplace')
        return msg + b'\n'
    if not isinstance(msg, str):
        msg = str(msg)
    return msg + '\n'

def _handler_emit(self, record):
    if self.stream is None:
        if self.mode != 'w' or not self._closed:
            self.stream = self._open()
    if self.stream:
        try:
            # issue 35046: merged two stream.writes into one.
            self.stream.write(_format_for_stream(self, record))
            self.flush()
        except RecursionError:  # See issue 36272
            raise
        except Exception:
            self.handleError(record)

class _StderrHandler(logging.StreamHandler):
    """``StreamHandler`` that writes each record in one ``write()``.

    Python 3.8+ does so already (issue 35046); 3.6 and 3.7 write the message and
    the newline separately, so another thread's stderr output could land between.
    """

    def emit(self, record):
        try:
            self.stream.write(self.format(record) + self.terminator)
            self.flush()
        except RecursionError:  # See issue 36272
            raise
        except Exception:
            self.handleError(record)

# In-place compression backends, in the order ``in_place_compression=True`` tries them
_COMPRESSION_CHAIN = ('zstd', 'xz', 'gzip', 'bz2')
_COMPRESSION_ALIASES = {'zst': 'zstd', 'lzma': 'xz'}
_COMPRESSION_SUFFIX = {'zstd': '.zst', 'xz': '.xz', 'gzip': '.gz', 'bz2': '.bz2'}

def _compression_available(name):
    """Return True if the module backing compression ``name`` can be imported."""
    try:
        if name == 'zstd':
            from compression import zstd
            _ = zstd
        elif name == 'xz':
            import lzma
            _ = lzma
        elif name == 'gzip':
            import zlib
            _ = zlib
        elif name == 'bz2':
            import bz2
            _ = bz2
        else:
            return False
        return True
    except ImportError:
        return False

def _resolve_compression(requested, disable_colors=...):
    """Map an ``in_place_compression`` value to an available backend name, or None.

    ``True`` (or ``...``) picks the first available of zstd, xz, gzip, bz2. A named
    backend whose module is missing falls through to the next one in that chain.

    Examples:
        >>> _resolve_compression(None) is None
        True
        >>> _resolve_compression('gzip')
        'gzip'
        >>> _resolve_compression('lzma')
        'xz'
    """
    if not requested:
        return None
    start = 0
    if requested is not True and requested is not ...:
        name = _COMPRESSION_ALIASES.get(requested, requested)
        if name in _COMPRESSION_CHAIN:
            start = _COMPRESSION_CHAIN.index(name)
        else:
            printWithColor(f'Invalid in_place_compression {requested!r}, using the default chain instead',
                           'warning', disable_colors=disable_colors, file=sys.stderr)
            requested = True
    for name in _COMPRESSION_CHAIN[start:]:
        if _compression_available(name):
            if requested is not True and requested is not ... and name != _COMPRESSION_CHAIN[start]:
                printWithColor(f'{_COMPRESSION_CHAIN[start]} compression is not available in this Python build, using {name} instead',
                               'warning', disable_colors=disable_colors, file=sys.stderr)
            return name
    printWithColor('No compression module is available in this Python build, writing uncompressed logs',
                   'warning', disable_colors=disable_colors, file=sys.stderr)
    return None

# Longest single wait of the background flusher. Event.wait() raises OverflowError for a
# timeout of threading.TIMEOUT_MAX or more (and for inf), so a long interval is waited in
# slices of this length; it is far below TIMEOUT_MAX on every platform.
_FLUSH_WAIT_MAX = 3600
# Bounds, in seconds, of the pause before a flusher that died is started again
_FLUSHER_RESTART_MIN = 1
_FLUSHER_RESTART_MAX = 3600

def _check_flush_interval(flush_interval):
    """Return ``flush_interval`` as a float; raise unless it is a number that is not NaN.

    The interval is used in float arithmetic (against ``time.monotonic()``) and as a wait
    timeout, which a Decimal or a numpy scalar does not support, so callers keep the float.

    Examples:
        >>> _check_flush_interval(30)
        30.0
        >>> _check_flush_interval(float('inf'))
        inf
        >>> _check_flush_interval(float('nan'))
        Traceback (most recent call last):
        ...
        ValueError: flush_interval must not be NaN
    """
    if math.isnan(flush_interval):  # TypeError for anything that is not a real number
        raise ValueError('flush_interval must not be NaN')
    return float(flush_interval)

class _CompressedFileHandler(logging.FileHandler):
    """Base for handlers that write a compressed log stream.

    Compressing well needs batching, but whatever still sits in the compressor
    is lost if the process dies. Records are therefore written without
    flushing, and the stream is flushed when a WARNING or higher record is
    written, or once ``flush_interval`` seconds have passed since the last
    flush (checked on every record, and by a background thread so a quiet
    tail still reaches the disk). ``flush_interval=0`` (or below) flushes every record, and an infinite one
    never flushes on a timer: only a WARNING or higher record, ``close()`` and
    interpreter shutdown flush then. No background thread runs in that case.

    gzip and zstd can flush mid-stream. xz and bz2 cannot, so for them a
    flush finishes the current stream and the next record starts a new one;
    the decompressors read the concatenated streams as one file.
    """
    # True when the format cannot flush mid-stream, so a flush finishes the stream
    finish_stream_on_flush = False
    default_compresslevel = None

    def __init__(self, filename, mode='a', encoding=None, delay=False, compresslevel=None, flush_interval=30):
        flush_interval = _check_flush_interval(flush_interval)
        # set before FileHandler.__init__, which opens the stream unless delay is set
        self.compresslevel = self.default_compresslevel if compresslevel is None else compresslevel
        self.flush_interval = flush_interval
        self._dirty = False
        self._last_flush = time.monotonic()
        self._flusher_stop = None
        # a flusher that could not be started, or is gone, is not started again before this
        # time (time.monotonic())
        self._flusher_retry_at = 0
        # True while background flushes keep failing, so only the first is reported
        self._flush_failing = False
        if 'b' not in mode:
            mode += 't'
        super().__init__(filename, mode, encoding, delay)

    def _open_compressed(self, opener, **kwargs):
        """Open the log through ``opener`` (e.g. ``gzip.open``) over a no-follow raw file."""
        if 'b' not in self.mode:
            kwargs['encoding'] = self.encoding
        raw = _open_append_nofollow(self.baseFilename)
        try:
            return _OwningStream(opener(raw, self.mode, **kwargs), raw)
        except BaseException:
            raw.close()
            raise

    def emit(self, record):
        try:
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(_format_for_stream(self, record))
            self._dirty = True
            if (record.levelno >= logging.WARNING or self.flush_interval <= 0
                    or time.monotonic() - self._last_flush >= self.flush_interval):
                self._flush_stream()
            elif (self._flusher_stop is None and math.isfinite(self.flush_interval)
                    and time.monotonic() >= self._flusher_retry_at):
                self._start_flusher()
        except RecursionError:  # See issue 36272
            raise
        except Exception:
            self.handleError(record)

    def _flush_stream(self):
        # caller holds the handler lock
        if self._dirty and self.stream is not None:
            try:
                if self.finish_stream_on_flush:
                    # Detach first: a stream whose close() fails is closed anyway (the
                    # compressor and the raw file both), so the next record must start a
                    # new stream, and what the failed one held cannot be retried. Nothing
                    # is recovered: part of the failed stream may already be on disk, and
                    # the stream started after it then leaves the rest of that file
                    # undecodable (a known limitation, as is a signal handler that logs
                    # while a stream is being finished).
                    stream, self.stream = self.stream, None
                    self._dirty = False
                    stream.close()
                else:
                    # on failure the stream stays open and dirty, so the next flush retries
                    self.stream.flush()
            except Exception:
                # back off: retry after another interval, not on every record or tick
                self._last_flush = time.monotonic()
                raise
        self._dirty = False
        self._last_flush = time.monotonic()
        self._flush_failing = False

    def flush(self):
        self.acquire()
        try:
            self._flush_stream()
        finally:
            self.release()

    def _start_flusher(self):
        stop = threading.Event()
        self._flusher_stop = stop
        thread = threading.Thread(target=self._flush_loop, args=(stop,), name='teeLogger-flush')
        thread.daemon = True
        try:
            thread.start()
        except BaseException:
            self._flusher_gave_up()
            raise

    def _flusher_gave_up(self, stop=None):
        """Allow a new flusher, but not before a pause of about one flush interval.

        Only for a flusher that is gone (it could not be started, or something escaped
        the loop): a later record starts another one, so buffered records do not sit
        unflushed for good. One that is gone at once every time would otherwise cost a
        thread (and a traceback) per record, so it is replaced at most once per interval,
        within ``_FLUSHER_RESTART_MIN``..``_FLUSHER_RESTART_MAX`` seconds.
        """
        if stop is None or self._flusher_stop is stop:
            self._flusher_stop = None
            pause = min(max(self.flush_interval, _FLUSHER_RESTART_MIN), _FLUSHER_RESTART_MAX)
            self._flusher_retry_at = time.monotonic() + pause

    def _flush_loop(self, stop):
        # Seconds to wait at least before the next tick after a tick failed; it doubles on
        # every failure up to the flush interval and starts over after a flush that worked
        backoff = 0.0
        reported = False
        try:
            while True:
                if self._dirty:
                    timeout = self.flush_interval - (time.monotonic() - self._last_flush)
                else:
                    timeout = self.flush_interval
                if stop.wait(min(max(timeout, backoff, 0.01), _FLUSH_WAIT_MAX)):
                    return
                try:
                    self.acquire()
                    try:
                        if stop.is_set():
                            return
                        if self._dirty and time.monotonic() - self._last_flush >= self.flush_interval:
                            self._background_flush()
                            backoff = 0.0
                            reported = False
                    finally:
                        self.release()
                except Exception:
                    # A tick failed for a reason of its own (a transient error, a handler or
                    # a formatter that misbehaves). Dying here would leave the buffered
                    # records without a timer until some later record starts a new flusher
                    # (and none may come), so report it once and try again, backing off.
                    if not reported:
                        reported = True
                        self._report_flusher_failure()
                    backoff = min(max(backoff * 2, min(self.flush_interval, 0.5)),
                                  max(self.flush_interval, 0.01), _FLUSH_WAIT_MAX)
        except BaseException:
            # should this thread die anyway, let a later emit() start another one
            self.acquire()
            try:
                self._flusher_gave_up(stop)
            finally:
                self.release()
            raise

    def _report_flusher_failure(self):
        # called from an except block, for the exception being handled
        try:
            self.handleError(logging.makeLogRecord({
                'name': 'teeLogger', 'levelno': logging.ERROR, 'levelname': 'ERROR',
                'msg': 'Background flusher of %s failed, retrying', 'args': (self.baseFilename,),
            }))
        except Exception:
            pass

    def _background_flush(self):
        # caller holds the handler lock
        try:
            self._flush_stream()
        except Exception:
            # Report it the way emit() does (handleError), but once per run of failures:
            # a full disk fails every tick until space is freed. The thread carries on,
            # and a flush that can be retried stays dirty for the next tick.
            if not self._flush_failing:
                self._flush_failing = True
                self.handleError(logging.makeLogRecord({
                    'name': 'teeLogger', 'levelno': logging.ERROR, 'levelname': 'ERROR',
                    'msg': 'Background flush of %s failed', 'args': (self.baseFilename,),
                }))

    def close(self):
        # Only signal the flusher: close() may run with the handler lock held
        # (logging.shutdown), so waiting for the thread here could deadlock.
        if self._flusher_stop is not None:
            self._flusher_stop.set()
            self._flusher_stop = None
        super().close()

_LOG_LEVELS = {
    'debug': logging.DEBUG,
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR,
    'critical': logging.CRITICAL,
}

def _with_exception_summary(msg):
    """Append ``ExcType: message`` of the exception being handled, if any, to ``msg``."""
    exc_type, exc_value = sys.exc_info()[:2]
    if exc_type is None:
        return str(msg)
    import traceback
    return f'{msg}: {traceback.format_exception_only(exc_type, exc_value)[-1].strip()}'

class teeLogger:
    """Logger that tees messages to a file and optionally to stdout.

    Log files live under ``{systemLogFileDir}/{programName}_log/YYYY-MM-DD/``.
    A long-running process rolls over to a new day folder/file at local
    midnight. On Unix, ``{programName}_latest.log`` (or ``.gz``/``.bz2``/
    ``.xz``/``.zst`` when compressed) symlinks to the active log file.

    ``programName`` is also the stdlib ``logging`` logger name. Constructing
    another ``teeLogger`` with the same name reuses that logger and replaces
    the previous file handler so only one active log file remains attached.
    Use distinct names for independent log files.

    File-only methods (``debug``, ``info``, ``ok``, ``warning``, ``error``,
    ``critical``, ``exception``, ``log``) never print to stdout. Console
    mirroring is limited to ``tee*`` methods and ``printTable``, and only when
    ``suppressPrintout`` is false. The logger's own messages (the "Log file:"
    banner, fallbacks, cleanup failures) go to stderr.

    In-place compressed logs are flushed to disk at most ``flush_interval``
    seconds after a record is written, and immediately for WARNING and above,
    so a crash loses at most that window of lower-level records. Readers see
    everything up to the last flush; ``gzip``/``zstd`` tools may warn about the
    unfinished end of a file that is still being written.

    Args:
        systemLogFileDir: Root directory for log folders. If it is not
            writable, falls back to a private ``teeLogger-<uid>`` directory
            (mode 0700) under the system temp dir. Log files are never opened
            through a symlink.
        programName: Stdlib logger name and log file prefix. Defaults to the
            caller's file name without its extension.
        compressLogAfterMonths: Compress day-folders older than this many months
            (``0`` disables). Default ``2``.
        deleteLogAfterYears: Delete day-folders older than this many years
            (``0`` disables). Default ``2``.
        suppressPrintout: Suppress console output. Defaults to ``True`` when
            stdout is not a TTY.
        fileDescriptorLength: Width of the ``filename:line`` field in log lines.
        noLog: If True, disable file logging entirely.
        callerStackDepth: ``-1`` (default) auto-resolves the direct caller
            outside ``Tee_Logger``; non-negative values select an explicit
            stack offset. Per-call overrides are supported on log methods.
        disable_colors: Disable ANSI colors for console helpers. The default
            (``...``) disables them when the stream is not a terminal.
        encoding: Text encoding for log files (default ``utf-8``).
        in_place_compression: Write compressed logs directly (``zstd``/``zst``,
            ``xz``/``lzma``, ``gzip``, ``bz2``), or ``True`` for the first of
            those available in this Python build. A backend whose module is
            missing falls through to the next one in that order (``zstd`` needs
            Python 3.14+).
        collapse_single_day_logs: One log file per day. Defaults to ``True`` when
            ``in_place_compression`` is set.
        compression_level: Backend-specific level/preset (optional). Note that
            ``xz`` presets 7-9 use several hundred MB of RAM per logger.
        binary_mode: Open log files in binary append mode (default ``True``).
        flush_interval: Seconds between flushes of compressed logs (default
            ``30``); ``0`` flushes every record, which compresses far worse, and
            ``float('inf')`` flushes only on WARNING and above, at close and at
            shutdown. NaN raises ``ValueError``.

    Examples:
        >>> tl = teeLogger(noLog=True, suppressPrintout=True, programName='doctest')
        >>> tl.noLog
        True
        >>> tl.info('silent example')  # doctest: +ELLIPSIS
    """

    class GZipFileHandler(_CompressedFileHandler):
        """Write log records directly to a gzip-compressed file."""
        default_compresslevel = 1

        def _open(self):
            import gzip
            return self._open_compressed(gzip.open, compresslevel=self.compresslevel)

    class BZ2FileHandler(_CompressedFileHandler):
        """Write log records directly to a bzip2-compressed file."""
        default_compresslevel = 1
        finish_stream_on_flush = True

        def _open(self):
            import bz2
            return self._open_compressed(bz2.open, compresslevel=self.compresslevel)

    class XZFileHandler(_CompressedFileHandler):
        """Write log records directly to an lzma/xz-compressed file."""
        default_compresslevel = 1
        finish_stream_on_flush = True

        def _open(self):
            import lzma
            return self._open_compressed(lzma.open, preset=self.compresslevel)

    class ZSTDFileHandler(_CompressedFileHandler):
        """Write log records directly to a zstd-compressed file."""
        default_compresslevel = 3

        def _open(self):
            from compression import zstd
            return self._open_compressed(zstd.open, level=self.compresslevel)

    class BinFileHandler(logging.FileHandler):
        """Write log records to a plain file with optional binary mode."""
        def __init__(self, filename, mode='a', encoding=None, delay=False):
            if 'b' in mode:
                super().__init__(filename, mode, encoding=None,delay=delay)
            else:
                super().__init__(filename, mode, encoding=encoding, delay=delay)
            self.encoding = encoding

        def _open(self):
            return _open_append_nofollow(self.baseFilename, self.mode, self.encoding)

        def emit(self, record):
            _handler_emit(self, record)
        
    def __init__(self, systemLogFileDir='.', programName=None, compressLogAfterMonths=2,
                 deleteLogAfterYears=2, suppressPrintout=..., fileDescriptorLength=15,
                 noLog=False,callerStackDepth=-1,disable_colors=..., encoding = None,
                 in_place_compression = None, collapse_single_day_logs = ...,compression_level=...,
                 binary_mode = True, flush_interval = 30):
        """Initialize handlers, log paths, and run log maintenance if needed."""
        if suppressPrintout is ...:
            # determine if we want to suppress printout by if the output is a terminal
            suppressPrintout = not _stream_is_tty(sys.stdout)
        self.disable_colors = disable_colors
        self.callerStackDepth = callerStackDepth
        if not programName:
            callerFile, _ = getCallerInfo(i=self.callerStackDepth)
            programName = os.path.splitext(callerFile)[0] or callerFile
        self.name = programName
        self.currentDateTime = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.noLog = noLog
        self.compressLogAfterMonths = compressLogAfterMonths
        self.deleteLogAfterYears = deleteLogAfterYears
        self.suppressPrintout = suppressPrintout
        self.fileDescriptorLength = fileDescriptorLength
        if not encoding:
            encoding = 'utf-8'
        self.encoding = encoding
        self.in_place_compression = _resolve_compression(in_place_compression, disable_colors=self.disable_colors)
        self.flush_interval = _check_flush_interval(flush_interval)
        if collapse_single_day_logs is ...:
            collapse_single_day_logs = bool(self.in_place_compression)
        self.collapse_single_day_logs = collapse_single_day_logs
        self.version = version
        self._binary_mode = binary_mode
        self._compression_level = compression_level
        self._latest_suffix = None
        # no rollover until a log file is open
        self._next_rollover = float('inf')
        # see _rollover for why this is an RLock plus a flag
        self._rollover_lock = threading.RLock()
        self._rolling_over = False
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self._clear_handlers()
        if systemLogFileDir in ['/dev/null', '/dev/stdout', '/dev/stderr']:
            self.noLog = True
        if self.noLog:
            self.systemLogFileDir = '/dev/null'
            self.logsDir = '/dev/null'
            self.logFileDir = '/dev/null'
            self.logFileName = '/dev/null'
            self._add_handler(logging.NullHandler())
            return
        if not self._try_file_logging(os.path.abspath(systemLogFileDir)):
            self._diagnose('Failed to create log file! Trying a private directory under the system temp dir')
            fallbackDir = _private_fallback_dir(self.disable_colors)
            if not fallbackDir or not self._try_file_logging(fallbackDir):
                self._diagnose('Failed to create a fallback log file, logging to stderr')
                handler = _StderrHandler()
                handler.setFormatter(logging.Formatter(
                    '%(asctime)s [%(levelname)s] [%(callerFileLocation)s] %(message)s',
                ))
                self._add_handler(handler)
        self.info(f'>>>>>>>>>>>>>>>>>>>Starting {programName} at {self.currentDateTime}<<<<<<<<<<<<<<<<<')
        self._start_background_cleanup()

    def _diagnose(self, msg, level='error'):
        """Report a problem with the logger itself on stderr, even when printout is suppressed.

        Best effort: it never raises. A report is made when something else has already
        gone wrong (a failed close, a failed rollover), often by a stderr that is itself
        broken (EIO from a vanished terminal, EPIPE); raising from here would abort the
        recovery the caller is in the middle of.
        """
        try:
            printWithColor(msg, level, disable_colors=self.disable_colors, file=sys.stderr)
        except Exception:
            pass

    def _add_handler(self, handler):
        handler._teeLoggerOwned = True
        self.logger.addHandler(handler)

    @staticmethod
    def _is_owned(handler):
        return isinstance(handler, logging.FileHandler) or getattr(handler, '_teeLoggerOwned', False)

    def _clear_handlers(self):
        """Detach and close the handlers a teeLogger attached to this logger name."""
        owned = [handler for handler in self.logger.handlers if self._is_owned(handler)]
        for handler in owned:
            self.logger.removeHandler(handler)
        self._close_handlers(owned)

    def _replace_handlers(self, handler):
        """Make ``handler`` the logger's only teeLogger handler, with no gap and no overlap.

        Other threads log without any lock of ours, so the logger must have a handler at
        every moment (a record that finds none is dropped, or only reaches
        ``logging.lastResort``), and must not have the old and the new one together (the
        record would be written to both files). Rebinding ``handlers`` to a new list does
        both in one step: a thread already dispatching a record keeps the old list and
        finishes writing to the old handler, which is closed only afterwards.
        """
        handler._teeLoggerOwned = True
        current = list(self.logger.handlers)
        old = [h for h in current if self._is_owned(h)]
        self.logger.handlers = [h for h in current if h not in old] + [handler]
        self._close_handlers(old)

    def _close_handlers(self, handlers):
        for handler in handlers:
            try:
                handler.close()
            except Exception as e:
                # e.g. a full disk failing the old log's last flush at a rollover:
                # report it and carry on, rather than leave the logger without its new handler
                self._diagnose(f'Failed to close {getattr(handler, "baseFilename", handler)} ({e})')

    def _set_log_paths(self, systemLogFileDir):
        day = self.currentDateTime.partition("_")[0]
        self.systemLogFileDir = systemLogFileDir
        self.logsDir = os.path.join(self.systemLogFileDir, self.name + '_log')
        self.logFileDir = os.path.join(self.logsDir, day)
        stamp = day if self.collapse_single_day_logs else self.currentDateTime
        self.logFileName = os.path.join(self.logFileDir, self.name + '_' + stamp + '.log')

    def _try_file_logging(self, systemLogFileDir):
        """Point the logger at a log file under ``systemLogFileDir``; return False on failure."""
        self._set_log_paths(systemLogFileDir)
        try:
            handler = self._open_log_file()
        except Exception as e:
            self._diagnose(e)
            return False
        self._clear_handlers()
        self._add_handler(handler)
        self._link_latest_log()
        if not self.suppressPrintout:
            printWithColor('Log file: ' + self.logFileName, 'info',
                           disable_colors=self.disable_colors, file=sys.stderr)
        self._next_rollover = _next_midnight(time.time())
        return True

    def _open_log_file(self):
        """Create the day folder and return a formatted handler for ``logFileName``."""
        os.makedirs(self.logFileDir, exist_ok=True)
        handler, self._latest_suffix = self._make_log_handler(self._binary_mode, self._compression_level)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)-8s] [%(callerFileLocation)s] %(message)s',
        ))
        return handler

    def _make_log_handler(self, binary_mode, compression_level):
        mode = 'ab' if binary_mode else 'a'
        if not self.in_place_compression:
            return self.BinFileHandler(self.logFileName, encoding=self.encoding, mode=mode), None
        suffix = _COMPRESSION_SUFFIX[self.in_place_compression]
        self.logFileName += suffix
        handler_class = {
            'gzip': self.GZipFileHandler,
            'bz2': self.BZ2FileHandler,
            'xz': self.XZFileHandler,
            'zstd': self.ZSTDFileHandler,
        }[self.in_place_compression]
        handler = handler_class(
            self.logFileName, mode=mode, encoding=self.encoding,
            compresslevel=None if compression_level is ... else compression_level,
            flush_interval=self.flush_interval,
        )
        return handler, suffix

    def _link_latest_log(self):
        """Point ``{programName}_latest.log[.suffix]`` at the active log; failures only warn."""
        if os.name == 'nt':
            return
        base = os.path.join(self.logsDir, self.name + '_latest.log')
        latest = base + (self._latest_suffix or '')
        # build the link under a private name and rename it over the old one, so
        # concurrent starts never see a missing link or trip over each other
        tmp = f'{latest}.tmp-{os.getpid()}-{threading.get_ident()}'
        try:
            os.symlink(os.path.relpath(self.logFileName, self.logsDir), tmp)
            os.replace(tmp, latest)
        except OSError as e:
            _remove_quietly(tmp)
            self._diagnose(f'Could not update {latest} ({e}); logging to {self.logFileName}', 'warning')
            return
        for suffix in [''] + list(_COMPRESSION_SUFFIX.values()):
            stale = base + suffix
            if stale != latest and os.path.islink(stale):
                _remove_quietly(stale)

    def _rollover(self):
        """Switch to a new day's log folder and file once local midnight has passed."""
        # A signal handler that logs runs on the main thread between bytecodes, so it can
        # land in here while the main thread is mid-rollover. The RLock lets it back in
        # instead of deadlocking, and the flag sends it straight on to log to the current
        # handler instead of starting a second rollover inside this one. A thread that
        # gets here while another is rolling over waits on the lock, then finds the
        # rollover done. A thread that checks _next_rollover after it moved on does not
        # come here and just logs: the new handler replaces the old one in a single
        # step (_replace_handlers) before the old one is closed, so it always has a
        # live handler to write to, the old log until the switch and the new one after.
        with self._rollover_lock:
            if self._rolling_over:
                return
            self._rolling_over = True
            try:
                now = time.time()
                if now < self._next_rollover:
                    return  # another thread already rolled over
                previous = (self.currentDateTime, self.logFileDir, self.logFileName)
                self.currentDateTime = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H-%M-%S")
                self._set_log_paths(self.systemLogFileDir)
                try:
                    handler = self._open_log_file()
                except Exception as e:
                    self.currentDateTime, self.logFileDir, self.logFileName = previous
                    self._next_rollover = now + 3600
                    self._diagnose(f'Failed to roll over to a new day log ({e}), still writing {self.logFileName}')
                    return
                self._next_rollover = _next_midnight(now)
                # closing the old handler also finishes its compressed stream, which can
                # take a while: switch first, so records logged meanwhile have a handler
                self._replace_handlers(handler)
                self._link_latest_log()
            finally:
                self._rolling_over = False
        self.info(f'Continued from {previous[2]}')
        self._start_background_cleanup()

    def _plan_cleanup(self):
        """Decide which entries under ``logsDir`` to compress or delete.

        Returns ``(path, action)`` pairs; calling ``action()`` does the work.
        """
        if self.noLog or not os.path.isdir(self.logsDir):
            return []
        now = time.time()
        deleteAfter = self.deleteLogAfterYears * 365 * 24 * 3600
        compressAfter = self.compressLogAfterMonths * 30 * 24 * 3600
        tasks = []
        for dirName in sorted(os.listdir(self.logsDir)):
            match = _LOG_DIR_RE.match(dirName)
            if not match:
                continue
            day, kind = match.group(1), match.group(2) or ''
            currentPath = os.path.join(self.logsDir, dirName)
            try:
                if kind.startswith(('.compressing-', '.deleting-')) and now - os.path.getmtime(currentPath) < _STALE_SECONDS:
                    continue  # another process is working on it right now
                age = now - _day_timestamp(day, currentPath)
            except OSError as e:
                self._diagnose(f'Failed to get the age of {dirName} ({e}), skipping')
                continue
            if not os.access(currentPath, os.W_OK):
                self.info(f'Skipping {currentPath} as it is not writable')
                continue
            if kind.startswith('.deleting-'):
                tasks.append((currentPath, functools.partial(_remove_path, currentPath)))
                continue
            if self.deleteLogAfterYears and age > deleteAfter:
                message = f'Deleting log dir {dirName} as it is older than {self.deleteLogAfterYears} years'
                action = functools.partial(_remove_path, currentPath)
            elif self.compressLogAfterMonths and kind != '.tar.xz' and age > compressAfter:
                message = f'Compressing log dir {dirName} as it is older than {self.compressLogAfterMonths} months'
                action = functools.partial(compress_folder, currentPath, disable_colors=self.disable_colors)
            else:
                continue
            if kind != '.tar.xz' and _recently_written(currentPath, now):
                self.info(f'Skipping {currentPath} as a file in it was modified within the last day')
                continue
            self.teelog(message, 'info')
            tasks.append((currentPath, action))
        return tasks

    def _run_cleanup_tasks(self, tasks):
        for path, action in tasks:
            try:
                action()
            except Exception as e:
                message = f'Log cleanup failed for {path}: {e}'
                self._diagnose(message)
                self.log_with_caller_info('error', message, callerStackDepth=2)

    def _start_background_cleanup(self):
        """Plan log maintenance now and run it in a background thread."""
        try:
            tasks = self._plan_cleanup()
        except Exception as e:
            self._diagnose(f'Failed to scan {self.logsDir} for old logs: {e}')
            return
        if tasks:
            # Not a daemon: interpreter exit waits for an archive in progress to finish.
            threading.Thread(target=self._run_cleanup_tasks, args=(tasks,), name='teeLogger-cleanup').start()

    def cleanup_old_logs(self):
        """Compress or delete day-folders under ``logsDir`` based on age settings (blocking)."""
        self._run_cleanup_tasks(self._plan_cleanup())

    def log_with_caller_info(self, level, msg, callerStackDepth=..., exc_info=False):
        """Write ``msg`` at ``level`` with abbreviated caller file/line metadata.

        ``exc_info=True`` appends the traceback of the exception being handled.
        """
        if self.noLog:
            return
        if time.time() >= self._next_rollover:
            self._rollover()
        if callerStackDepth is ...:
            callerStackDepth = self.callerStackDepth
        filename, lineno = getCallerInfo(i=callerStackDepth)
        extra = {'callerFileLocation': abbreviate_filename(filename, lineno, target_length=self.fileDescriptorLength)}
        self.logger.log(_LOG_LEVELS.get(level, logging.INFO), msg, extra=extra, exc_info=exc_info)

    def teeok(self, msg, callerStackDepth=...):
        """Print ``msg`` in green and log it at info level."""
        if not self.suppressPrintout:
            printWithColor(msg, 'okgreen',disable_colors=self.disable_colors)
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def printTable(self, data, callerStackDepth=..., header=None):
        """Format ``data`` as a table, print it, and log it at info level."""
        tableStr = pretty_format_table(data, header=header)
        if not self.suppressPrintout:
            printWithColor(tableStr, 'info',disable_colors=self.disable_colors)
        self.log_with_caller_info('info', msg='\n' + tableStr, callerStackDepth=callerStackDepth)

    def ok(self, msg, callerStackDepth=...):
        """Log ``msg`` at info level without printing to stdout."""
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def teeprint(self, msg, callerStackDepth=...):
        """Print ``msg`` and log it at info level."""
        if not self.suppressPrintout:
            printWithColor(msg, 'info',disable_colors=self.disable_colors)
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def info(self, msg, callerStackDepth=...):
        """Log ``msg`` at info level without printing to stdout."""
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def teedebug(self, msg, callerStackDepth=...):
        """Print ``msg`` and log it at debug level."""
        self.teelog(msg, 'debug', callerStackDepth=callerStackDepth)

    def debug(self, msg, callerStackDepth=...):
        """Log ``msg`` at debug level without printing to stdout."""
        self.log_with_caller_info('debug', msg, callerStackDepth=callerStackDepth)

    def teewarning(self, msg, callerStackDepth=...):
        """Print ``msg`` as a warning and log it at warning level."""
        self.teelog(msg, 'warning', callerStackDepth=callerStackDepth)

    def warning(self, msg, callerStackDepth=...):
        """Log ``msg`` at warning level without printing to stdout."""
        self.log_with_caller_info('warning', msg, callerStackDepth=callerStackDepth)

    def teeerror(self, msg, callerStackDepth=...):
        """Print ``msg`` as an error and log it at error level."""
        if not self.suppressPrintout:
            printWithColor(msg, 'error',disable_colors=self.disable_colors)
        self.log_with_caller_info('error', msg, callerStackDepth=callerStackDepth)

    def error(self, msg, callerStackDepth=...):
        """Log ``msg`` at error level without printing to stdout."""
        self.log_with_caller_info('error', msg, callerStackDepth=callerStackDepth)

    def teecritical(self, msg, callerStackDepth=...):
        """Print ``msg`` as critical and log it at critical level."""
        self.teelog(msg, 'critical', callerStackDepth=callerStackDepth)

    def critical(self, msg, callerStackDepth=...):
        """Log ``msg`` at critical level without printing to stdout."""
        self.log_with_caller_info('critical', msg, callerStackDepth=callerStackDepth)

    def teeexception(self, msg, callerStackDepth=...):
        """Print ``msg`` with a one-line summary of the exception being handled, and
        log it at error level with the full traceback."""
        if not self.suppressPrintout:
            printWithColor(_with_exception_summary(msg), 'error', disable_colors=self.disable_colors)
        self.log_with_caller_info('error', msg, callerStackDepth=callerStackDepth, exc_info=True)

    def exception(self, msg, callerStackDepth=...):
        """Log ``msg`` at error level with the traceback of the exception being handled."""
        self.log_with_caller_info('error', msg, callerStackDepth=callerStackDepth, exc_info=True)

    def teelog(self, msg, level, callerStackDepth=...):
        """Print ``msg`` with ``level`` styling and log at ``level``."""
        if not self.suppressPrintout:
            printWithColor(msg, level,disable_colors=self.disable_colors)
        self.log_with_caller_info(level, msg, callerStackDepth=callerStackDepth)


    def log(self, msg, level, callerStackDepth=...):
        """Log ``msg`` at the given ``level`` without printing to stdout."""
        self.log_with_caller_info(level, msg, callerStackDepth=callerStackDepth)

if __name__ == '__main__':
    import argparse
    import doctest

    parser = argparse.ArgumentParser(description='Tee_Logger module utilities')
    parser.add_argument('-V', '--version', action='version',
                        version=f'Tee_Logger {version} by {__author__}')
    parser.add_argument('--test', action='store_true', help='run doctests')
    args = parser.parse_args()
    if args.test:
        results = doctest.testmod(verbose='-v')
        raise SystemExit(1 if results.failed else 0)
    print(f'Tee_Logger {version} by {__author__}')