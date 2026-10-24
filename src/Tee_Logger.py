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
import importlib
import shutil
import stat
import tarfile
import subprocess
import sys
import tempfile
import threading
import time

version = '6.44'
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
    # The newline goes in with the text, so print() writes the line with one write()
    # and another thread's output cannot land in between. No lock: a signal handler
    # that logs runs on the main thread, possibly while it is in here.
    print(line, end='', file=file)

def _report(msg, level='error', disable_colors=...):
    """Report a problem with the logger itself on stderr, even when printout is suppressed.

    Best effort: it never raises. A report is made when something else has already
    gone wrong, often to a stderr that is itself broken (EIO from a vanished
    terminal, EPIPE); raising from here would abort what the caller is recovering.
    """
    try:
        printWithColor(msg, level, disable_colors=disable_colors, file=sys.stderr)
    except Exception:
        pass

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
    _report(f'tar failed to compress {day} ({reason}), falling back to python implementation',
            'warning', disable_colors)
    return False

def _tar_with_tarfile(folder, day, tmpArchive, disable_colors=...):
    """Archive ``folder`` as ``day/`` into ``tmpArchive`` with :mod:`tarfile`."""
    try:
        with tarfile.open(tmpArchive, 'w:xz') as tar:
            tar.add(folder, arcname=day)
        return True
    except Exception as e:
        _remove_quietly(tmpArchive)
        _report(f'Failed to compress folder {day} due to {e}', 'error', disable_colors)
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
        _report(f'Failed to compress folder {day} due to {e}', 'error', disable_colors)
        return False

def _open_append_nofollow(path, mode='ab', encoding=None, buffering=-1):
    """Open ``path`` for appending, refusing to follow a symlink or open a non-regular file.

    Log paths are predictable, so in a directory other users can write to, a
    planted symlink would otherwise redirect our writes into any file we can write.
    """
    # O_NONBLOCK so a planted FIFO fails instead of blocking forever; a regular file ignores it
    flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
             | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    fd = os.open(path, flags, 0o666)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f'{path} is not a regular file')
        if 'b' in mode:
            return os.fdopen(fd, 'ab', buffering)
        return os.fdopen(fd, 'a', encoding=encoding)
    except BaseException:
        os.close(fd)
        raise

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
    _report(f'Refusing to log to {path}: it {problem}', 'error', disable_colors)
    return None

def _format_for_stream(handler, record):
    """Format ``record`` as one line for ``handler.stream`` (bytes in binary mode)."""
    line = handler.format(record) + '\n'
    if 'b' in handler.mode:
        return line.encode(handler.encoding, errors='namereplace')
    return line

class _LogFileHandler(logging.FileHandler):
    """A teeLogger log file: opened without following symlinks, and switched at midnight."""

    def _open(self):
        return _open_append_nofollow(self.baseFilename, self.mode, self.encoding)

    def switch_to(self, filename):
        """Write to ``filename`` from now on; what was logged before goes to the old file.

        It holds the handler lock, which every record takes, so each record lands
        in one file or the other.
        """
        self.acquire()
        try:
            try:
                self.flush()
            except Exception as e:
                _report(f'Failed to write the end of {self.baseFilename} ({e})')
            old_name, self.baseFilename = self.baseFilename, os.path.abspath(filename)
            try:
                stream = self._open()
            except BaseException:
                self.baseFilename = old_name
                raise
            stream, self.stream = self.stream, stream
        finally:
            self.release()
        if stream is not None:
            try:
                stream.close()
            except Exception as e:
                _report(f'Failed to close {old_name} ({e})')

# In-place compression backends, in the order ``in_place_compression=True`` tries them:
# the module whose compress() makes a whole gzip member (xz or bz2 stream, zstd frame),
# the file suffix, the default level, and the name of compress()'s level argument
_COMPRESSION_CHAIN = ('zstd', 'xz', 'gzip', 'bz2')
_COMPRESSION_BACKENDS = {
    'zstd': ('compression.zstd', '.zst', 3, 'level'),
    'xz': ('lzma', '.xz', 1, 'preset'),
    'gzip': ('gzip', '.gz', 1, 'compresslevel'),
    'bz2': ('bz2', '.bz2', 1, 'compresslevel'),
}
_COMPRESSION_ALIASES = {'zst': 'zstd', 'lzma': 'xz'}

def _compression_available(name):
    """Return True if the module backing compression ``name`` can be imported."""
    try:
        importlib.import_module(_COMPRESSION_BACKENDS[name][0])
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
            _report(f'Invalid in_place_compression {requested!r}, using the default chain instead',
                    'warning', disable_colors)
            requested = True
    for name in _COMPRESSION_CHAIN[start:]:
        if _compression_available(name):
            if requested is not True and requested is not ... and name != _COMPRESSION_CHAIN[start]:
                _report(f'{_COMPRESSION_CHAIN[start]} compression is not available in this Python build, using {name} instead',
                        'warning', disable_colors)
            return name
    _report('No compression module is available in this Python build, writing uncompressed logs',
            'warning', disable_colors)
    return None

# Longest single wait of the background flusher. Event.wait() raises OverflowError for a
# timeout of threading.TIMEOUT_MAX or more, so a long interval is waited in slices of this
# length; it is far below TIMEOUT_MAX on every platform.
_FLUSH_WAIT_MAX = 3600
# Bytes of records held in memory at most, whatever the interval
_FLUSH_BATCH_MAX = 1 << 20

class _CompressedFileHandler(_LogFileHandler):
    """Write log records to a compressed file, in batches.

    Records are held in memory and written as one whole gzip member (xz or bz2
    stream, zstd frame) with a single write(); the decompressors read the members
    back as one file. So the file always ends on a whole member: a crash loses only
    what was not yet written, and a later run or another process appending to the
    same file leaves it readable.

    The batch is written when a WARNING or higher record is logged, once
    ``flush_interval`` seconds have passed since the last write (checked on every
    record, and by a background thread so a quiet tail still reaches the disk), once
    it holds ``_FLUSH_BATCH_MAX`` bytes, and at close. ``flush_interval=0`` (or
    below) writes every record; an infinite one never writes on a timer and runs no
    thread. A batch that cannot be written is dropped, and the failure reported.
    """

    def __init__(self, filename, backend, level=None, flush_interval=30, encoding='utf-8'):
        module, _suffix, default_level, level_arg = _COMPRESSION_BACKENDS[backend]
        self._compress = functools.partial(importlib.import_module(module).compress,
                                           **{level_arg: default_level if level is None else level})
        self.flush_interval = flush_interval
        self._batch = []
        self._batch_size = 0
        self._last_flush = time.monotonic()
        self._flusher_stop = None
        super().__init__(filename, 'ab')
        self.encoding = encoding

    def _open(self):
        # unbuffered, so a member goes out in one write() and a short one is seen
        return _open_append_nofollow(self.baseFilename, buffering=0)

    def emit(self, record):
        try:
            if self.stream is None:
                self.stream = self._open()
            line = _format_for_stream(self, record)
            self._batch.append(line)
            self._batch_size += len(line)
            if (record.levelno >= logging.WARNING or self._batch_size >= _FLUSH_BATCH_MAX
                    or time.monotonic() - self._last_flush >= self.flush_interval):
                self.flush()
            elif self._flusher_stop is None and math.isfinite(self.flush_interval):
                # set first: a thread that cannot be started is not tried again
                self._flusher_stop = threading.Event()
                thread = threading.Thread(target=self._flush_loop, args=(self._flusher_stop,),
                                          name='teeLogger-flush')
                thread.daemon = True
                thread.start()
        except RecursionError:  # See issue 36272
            raise
        except Exception:
            self.handleError(record)

    def flush(self):
        """Write the batch as one compressed member; one that fails is dropped, and this raises."""
        self.acquire()
        try:
            batch, self._batch, self._batch_size = self._batch, [], 0
            self._last_flush = time.monotonic()
            if batch and self.stream is not None:
                data = self._compress(b''.join(batch))
                written = self.stream.write(data)
                if written < len(data):
                    # a full disk: cut the partial member, which would hide everything
                    # appended after it from the decompressors
                    os.ftruncate(self.stream.fileno(), self.stream.tell() - written)
                    raise OSError(f'wrote {written} of {len(data)} bytes to {self.baseFilename}')
        finally:
            self.release()

    def _flush_loop(self, stop):
        while True:
            if self._batch:
                timeout = self.flush_interval - (time.monotonic() - self._last_flush)
            else:
                timeout = self.flush_interval
            if stop.wait(min(max(timeout, 0.01), _FLUSH_WAIT_MAX)):
                return
            self.acquire()
            try:
                if self._batch and time.monotonic() - self._last_flush >= self.flush_interval:
                    self.flush()
            except Exception:
                self.handleError(logging.makeLogRecord({
                    'name': 'teeLogger', 'levelno': logging.ERROR, 'levelname': 'ERROR',
                    'msg': 'Background flush of %s failed', 'args': (self.baseFilename,),
                }))
            finally:
                self.release()

    def close(self):
        # Only signal the flusher: close() may run with the handler lock held
        # (logging.shutdown), so waiting for the thread here could deadlock.
        if self._flusher_stop is not None:
            self._flusher_stop.set()
        # a record that still reaches this handler (from a thread that looked it up
        # before it was closed) is written at once, as no flusher is left to do it
        self.flush_interval = 0
        super().close()

# systemLogFileDir values that mean "write no log file"
_NULL_DIRS = ('/dev/null', '/dev/stdout', '/dev/stderr')

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

    In-place compressed logs are written to disk at most ``flush_interval``
    seconds after a record is logged, and immediately for WARNING and above,
    so a crash loses at most that window of lower-level records. Each write is
    a whole gzip member (xz or bz2 stream, zstd frame), so the file stays
    readable however its writers end, and readers see everything written so far.

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
        binary_mode: Open uncompressed log files in binary append mode (default
            ``True``); compressed ones are always binary.
        flush_interval: Seconds between writes of compressed logs (default
            ``30``); ``0`` writes every record, which compresses far worse, and
            ``float('inf')`` (or NaN) writes only on WARNING and above, at close
            and at shutdown. It is taken as a float, so a value ``float()``
            refuses raises when a compressed log file will use it.

    Examples:
        >>> tl = teeLogger(noLog=True, suppressPrintout=True, programName='doctest')
        >>> tl.noLog
        True
        >>> tl.info('silent example')  # doctest: +ELLIPSIS
    """

    class BinFileHandler(_LogFileHandler):
        """Write log records to a plain file with optional binary mode."""
        def __init__(self, filename, mode='a', encoding=None, delay=False):
            super().__init__(filename, mode, encoding=None if 'b' in mode else encoding, delay=delay)
            self.encoding = encoding

        def emit(self, record):
            try:
                if self.stream is None:
                    self.stream = self._open()
                # one write per record (issue 35046)
                self.stream.write(_format_for_stream(self, record))
                self.flush()
            except RecursionError:  # See issue 36272
                raise
            except Exception:
                self.handleError(record)

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
        # only a compressed log file uses it, so it is not checked elsewhere
        if self.in_place_compression and not self.noLog and systemLogFileDir not in _NULL_DIRS:
            flush_interval = float(flush_interval)
        self.flush_interval = flush_interval
        if collapse_single_day_logs is ...:
            collapse_single_day_logs = bool(self.in_place_compression)
        self.collapse_single_day_logs = collapse_single_day_logs
        self.version = version
        self._binary_mode = binary_mode
        self._compression_level = None if compression_level is ... else compression_level
        self._suffix = _COMPRESSION_BACKENDS[self.in_place_compression][1] if self.in_place_compression else ''
        # no rollover until a log file is open
        self._next_rollover = float('inf')
        # an RLock, as a signal handler that logs can land in a rollover on its own thread
        self._rollover_lock = threading.RLock()
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self._clear_handlers()
        if systemLogFileDir in _NULL_DIRS:
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
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter(
                    '%(asctime)s [%(levelname)s] [%(callerFileLocation)s] %(message)s',
                ))
                self._add_handler(handler)
        self.info(f'>>>>>>>>>>>>>>>>>>>Starting {programName} at {self.currentDateTime}<<<<<<<<<<<<<<<<<')
        self._start_background_cleanup()

    def _diagnose(self, msg, level='error'):
        _report(msg, level, self.disable_colors)

    def _add_handler(self, handler):
        handler._teeLoggerOwned = True
        self.logger.addHandler(handler)

    def _clear_handlers(self):
        """Detach and close the handlers a teeLogger attached to this logger name."""
        for handler in [h for h in self.logger.handlers if getattr(h, '_teeLoggerOwned', False)]:
            self.logger.removeHandler(handler)
            try:
                handler.close()
            except Exception as e:
                self._diagnose(f'Failed to close {getattr(handler, "baseFilename", handler)} ({e})')

    def _set_log_paths(self, systemLogFileDir):
        day = self.currentDateTime.partition("_")[0]
        self.systemLogFileDir = systemLogFileDir
        self.logsDir = os.path.join(self.systemLogFileDir, self.name + '_log')
        self.logFileDir = os.path.join(self.logsDir, day)
        stamp = day if self.collapse_single_day_logs else self.currentDateTime
        self.logFileName = os.path.join(self.logFileDir, self.name + '_' + stamp + '.log' + self._suffix)

    def _try_file_logging(self, systemLogFileDir):
        """Point the logger at a log file under ``systemLogFileDir``; return False on failure."""
        self._set_log_paths(systemLogFileDir)
        try:
            os.makedirs(self.logFileDir, exist_ok=True)
            if self.in_place_compression:
                handler = _CompressedFileHandler(self.logFileName, self.in_place_compression,
                                                 self._compression_level, self.flush_interval, self.encoding)
            else:
                handler = self.BinFileHandler(self.logFileName, 'ab' if self._binary_mode else 'a', self.encoding)
        except Exception as e:
            self._diagnose(e)
            return False
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)-8s] [%(callerFileLocation)s] %(message)s',
        ))
        self._clear_handlers()
        self._add_handler(handler)
        self._link_latest_log()
        if not self.suppressPrintout:
            self._diagnose('Log file: ' + self.logFileName, 'info')
        self._next_rollover = _next_midnight(time.time())
        return True

    def _link_latest_log(self):
        """Point ``{programName}_latest.log[.suffix]`` at the active log; failures only warn."""
        if os.name == 'nt':
            return
        base = os.path.join(self.logsDir, self.name + '_latest.log')
        latest = base + self._suffix
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
        for suffix in [''] + [backend[1] for backend in _COMPRESSION_BACKENDS.values()]:
            stale = base + suffix
            if stale != latest and os.path.islink(stale):
                _remove_quietly(stale)

    def _rollover(self):
        """Switch to a new day's log folder and file once local midnight has passed."""
        with self._rollover_lock:
            now = time.time()
            if now < self._next_rollover:
                return  # another thread, or a signal handler that interrupted this one, came first
            self._next_rollover = _next_midnight(now)
            # the logger's log file handler: ours, or that of a later teeLogger of the same name
            handler = next((h for h in self.logger.handlers if isinstance(h, _LogFileHandler)), None)
            if handler is None:
                return
            previous = (self.currentDateTime, self.logFileDir, self.logFileName)
            self.currentDateTime = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H-%M-%S")
            self._set_log_paths(self.systemLogFileDir)
            try:
                os.makedirs(self.logFileDir, exist_ok=True)
                handler.switch_to(self.logFileName)
            except Exception as e:
                self.currentDateTime, self.logFileDir, self.logFileName = previous
                self._next_rollover = now + 3600
                self._diagnose(f'Failed to roll over to a new day log ({e}), still writing {self.logFileName}')
                return
            self._link_latest_log()
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
                age = now - datetime.datetime.strptime(day, '%Y-%m-%d').timestamp()
            except (OSError, ValueError) as e:  # ValueError: not a real date, e.g. 2020-13-45
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