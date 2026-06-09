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
import tarfile
import subprocess
import sys
try:
    import dateutil.parser
except ImportError:
    pass

version = '6.39'
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
    info = '\033[0m'
    debug = '\033[0m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

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
        # Find the last non-abbreviated word
        for i in range(len(parts) - 1, -1, -1):
            if parts[i].isalnum() and not parts[i].isupper():
                parts[i] = parts[i][0].upper()
                return ''.join(parts)
        # If no non-abbreviated word is found, we remove the last part
        return ''.join(parts[:-1])
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
        filename = abbreviate_last_word(filename)
    # Truncate the filename if it is still too long
    filename = filename[:fileNameMaxLen]
    lineNumberStr = shorten_line_number(int(lineNumber), target_length=target_length - len(filename) -1)
    strOut = f"{filename}:{lineNumberStr}".ljust(target_length)
    return strOut

def printWithColor(msg, level = 'info',disable_colors=False):
    """Print ``msg`` to stdout using ANSI colors for ``level``.

    When ``disable_colors`` is True, output is plain text prefixed with
    ``[LEVEL]``.

    Args:
        msg: Text to print.
        level: Color key (``info``, ``debug``, ``warning``, ``error``,
            ``critical``, ``ok``/``okgreen``, ``okblue``, ``okcyan``).
        disable_colors: If True, omit ANSI escape sequences.

    Examples:
        >>> import io, contextlib
        >>> buf = io.StringIO()
        >>> with contextlib.redirect_stdout(buf):
        ...     printWithColor('hello', 'warning', disable_colors=True)
        >>> buf.getvalue()
        '[WARNING] hello\\n'
    """
    if disable_colors:
        print(f'[{level.upper()}] {msg}')
    elif level == 'info':
        print(f'{bcolors.info}{msg}{bcolors.ENDC}')
    elif level == 'debug':
        print(f'{bcolors.debug}{msg}{bcolors.ENDC}')
    elif level == 'warning':
        print(f'{bcolors.warning}{msg}{bcolors.ENDC}')
    elif level == 'error':
        print(f'{bcolors.warning}{msg}{bcolors.ENDC}')
    elif level == 'critical':
        print(f'{bcolors.critical}{msg}{bcolors.ENDC}')
    elif level == 'ok' or level == 'okgreen':
        print(f'{bcolors.OKGREEN}{msg}{bcolors.ENDC}')
    elif level == 'okblue':
        print(f'{bcolors.OKBLUE}{msg}{bcolors.ENDC}')
    elif level == 'okcyan':
        print(f'{bcolors.OKCYAN}{msg}{bcolors.ENDC}')
    else:
        print(f'{bcolors.info}{msg}{bcolors.ENDC}')

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
        # flatten the 2D dict to a list of lists
        if isinstance(next(iter(data.values())), dict):
            tempData = [['key'] + list(next(iter(data.values())).keys())]
            tempData.extend( [[key] + list(value.values()) for key, value in data.items()])
            data = tempData
        else:
            # it is a dict of lists
            data = [[key] + list(value) for key, value in data.items()]
    elif not isinstance(data, list):
        data = list(data)
    # format the list into 2d list of list of strings
    if isinstance(data[0], dict):
        tempData = [data[0].keys()]
        tempData.extend([list(item.values()) for item in data])
        data = tempData
    data = [[str(item) for item in row] for row in data]
    num_cols = len(data[0])
    col_widths = [0] * num_cols
    # Calculate the maximum width of each column
    for c in range(num_cols):
        #col_widths[c] = max(len(row[c]) for row in data)
        # handle ansii escape sequences
        col_widths[c] = max(len(re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',row[c])) for row in data)
    if header:
        header_widths = [len(re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', col)) for col in header]
        col_widths = [max(col_widths[i], header_widths[i]) for i in range(num_cols)]
    # Build the row format string
    row_format = ' | '.join('{{:<{}}}'.format(width) for width in col_widths)
    # Print the header
    if not header:
        header = data[0]
        outTable = []
        outTable.append(row_format.format(*header))
        outTable.append('-+-'.join('-' * width for width in col_widths))
        for row in data[1:]:
            # if the row is empty, print an divider
            if not any(row):
                outTable.append('-+-'.join('-' * width for width in col_widths))
            else:
                outTable.append(row_format.format(*row))
    else:
        # pad / truncate header to appropriate length
        if isinstance(header,str):
            header = header.split(delimiter)
        if len(header) < num_cols:
            header += ['']*(num_cols-len(header))
        elif len(header) > num_cols:
            header = header[:num_cols]
        outTable = []
        outTable.append(row_format.format(*header))
        outTable.append('-+-'.join('-' * width for width in col_widths))
        for row in data:
            # if the row is empty, print an divider
            if not any(row):
                outTable.append('-+-'.join('-' * width for width in col_widths))
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

def _log_dir_date_key(dirName):
    """Extract ``YYYY-MM-DD`` key from a log directory name.

    Examples:
        >>> _log_dir_date_key('2020-01-01')
        '2020-01-01'
        >>> _log_dir_date_key('2020-01-01.tar.xz')
        '2020-01-01'
        >>> _log_dir_date_key('2020-01-01.txt')
        '2020-01-01.txt'
    """
    if dirName.endswith('.tar.xz'):
        return dirName[:-7]
    return dirName

def compress_folder(folderPath, disable_colors=False):
    """Compress a log day-folder to ``folderPath.tar.xz`` and remove the original.

    Uses ``tar`` and ``xz`` when available on the PATH; otherwise falls back to
    Python's ``tarfile`` module.

    Args:
        folderPath: Path to the directory to archive.
        disable_colors: Passed through to status messages on failure.

    Returns:
        True if compression succeeded, False otherwise.
    """
    if os.name != 'nt' and shutil.which('tar') and shutil.which('xz'):
        try:
            relativePath = os.path.basename(folderPath)
            subprocess.run(
                ['tar', '-caf', folderPath + '.tar.xz', '--remove-files', relativePath],
                cwd=os.path.dirname(folderPath),
            )
            return True
        except Exception as e:
            printWithColor(
                f'Failed to compress folder {relativePath} with tar due to {e}',
                'error',
                disable_colors=disable_colors,
            )
            printWithColor('Falling back to python implementation', 'warning', disable_colors=disable_colors)
    try:
        with tarfile.open(folderPath + '.tar.xz', 'w:xz') as tar:
            tar.add(folderPath, arcname=os.path.basename(folderPath))
        shutil.rmtree(folderPath)
        return True
    except Exception as e:
        printWithColor(f'Failed to compress folder due to {e}', 'error', disable_colors=disable_colors)
        return False

def _handler_emit(self, record):
    if self.stream is None:
        if self.mode != 'w' or not self._closed:
            self.stream = self._open()
    if self.stream:
        try:
            msg = self.format(record)
            # encode msg
            if 'b' in self.mode:
                if not isinstance(msg,bytes):
                    if not isinstance(msg, str):
                        msg = str(msg)
                    msg = msg.encode(self.encoding,errors='namereplace')
                msg += b'\n'
            else:
                if not isinstance(msg, str):
                    msg = str(msg)
                msg += '\n'
            stream = self.stream
            # issue 35046: merged two stream.writes into one.
            stream.write(msg)
            self.flush()
        except RecursionError:  # See issue 36272
            raise
        except Exception:
            self.handleError(record)

class teeLogger:
    """Logger that tees messages to a file and optionally to stdout.

    Log files live under ``{systemLogFileDir}/{programName}_log/YYYY-MM-DD/``.
    On Unix, ``{programName}_latest.log`` (or ``.gz``/``.xz``/``.zst`` when
    compressed) symlinks to the active log file.

    Args:
        systemLogFileDir: Root directory for log folders. Falls back to ``/tmp``
            if the primary path is not writable.
        programName: Logger name and log file prefix. Defaults to the caller's
            file name when omitted.
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
        disable_colors: Disable ANSI colors for console helpers.
        encoding: Text encoding for log files (default ``utf-8``).
        in_place_compression: Write compressed logs directly (``gzip``, ``bz2``,
            ``xz``/``lzma``, ``zstd``, or ``True`` for ``xz``). ``zstd`` needs
            Python 3.14+ with zstd support; otherwise falls back to ``xz``.
        collapse_single_day_logs: One log file per day. Defaults to ``True`` when
            ``in_place_compression`` is set.
        compression_level: Backend-specific level/preset (optional).
        binary_mode: Open log files in binary append mode (default ``True``).

    Examples:
        >>> tl = teeLogger(noLog=True, suppressPrintout=True, programName='doctest')
        >>> tl.noLog
        True
        >>> tl.info('silent example')  # doctest: +ELLIPSIS
    """

    class GZipFileHandler(logging.FileHandler):
        """Write log records directly to a gzip-compressed file."""
        def __init__(self, filename, mode='a', encoding=None, delay=False, compresslevel=1):
            self.compresslevel = compresslevel
            if 'b' not in mode:
                mode += 't'
            super().__init__(filename, mode, encoding, delay)
        
        def _open(self):
            import gzip
            if 'b' in self.mode:
                return gzip.open(self.baseFilename, self.mode, compresslevel=self.compresslevel)
            return gzip.open(self.baseFilename, self.mode, compresslevel=self.compresslevel, encoding=self.encoding)

        def emit(self, record):
            _handler_emit(self, record)

    class BZ2FileHandler(logging.FileHandler):
        """Write log records directly to a bzip2-compressed file."""
        def __init__(self, filename, mode='a', encoding=None, delay=False, compresslevel=1):
            self.compresslevel = compresslevel
            if 'b' not in mode:
                mode += 't'
            super().__init__(filename, mode, encoding, delay)
        
        def _open(self):
            import bz2
            if 'b' in self.mode:
                return bz2.open(self.baseFilename, self.mode, compresslevel=self.compresslevel)
            return bz2.open(self.baseFilename, self.mode, compresslevel=self.compresslevel, encoding=self.encoding)
        
        def emit(self, record):
            _handler_emit(self, record)
        
    class XZFileHandler(logging.FileHandler):
        """Write log records directly to an lzma/xz-compressed file."""
        def __init__(self, filename, mode='a', encoding=None, delay=False, preset=1):
            self.preset = preset
            if 'b' not in mode:
                mode += 't'
            super().__init__(filename, mode, encoding, delay)
        
        def _open(self):
            import lzma
            if 'b' in self.mode:
                return lzma.open(
                    self.baseFilename, 
                    self.mode, 
                    preset=self.preset
                )
            return lzma.open(
                self.baseFilename, 
                self.mode, 
                preset=self.preset,
                encoding=self.encoding
            )
        
        def emit(self, record):
            _handler_emit(self, record)

    class ZSTDFileHandler(logging.FileHandler):
        """Write log records directly to a zstd-compressed file."""
        def __init__(self, filename, mode='a', encoding=None, delay=False, level=3):
            self.level = level
            if 'b' not in mode:
                mode += 't'
            super().__init__(filename, mode, encoding, delay)

        def _open(self):
            from compression import zstd
            if 'b' in self.mode:
                return zstd.open(self.baseFilename, self.mode, level=self.level)
            return zstd.open(self.baseFilename, self.mode, level=self.level, encoding=self.encoding)

        def emit(self, record):
            _handler_emit(self, record)
    
    class BinFileHandler(logging.FileHandler):
        """Write log records to a plain file with optional binary mode."""
        def __init__(self, filename, mode='a', encoding=None, delay=False):
            if 'b' in mode:
                super().__init__(filename, mode, encoding=None,delay=delay)
            else:
                super().__init__(filename, mode, encoding=encoding, delay=delay)
            self.encoding = encoding
        
        def emit(self, record):
            _handler_emit(self, record)
        
    def __init__(self, systemLogFileDir='.', programName=None, compressLogAfterMonths=2, 
                 deleteLogAfterYears=2, suppressPrintout=..., fileDescriptorLength=15,
                 noLog=False,callerStackDepth=-1,disable_colors=False, encoding = None,
                 in_place_compression = None, collapse_single_day_logs = ...,compression_level=...,
                 binary_mode = True):
        """Initialize handlers, log paths, and run log maintenance if needed."""
        if suppressPrintout is ...:
            # determine if we want to suppress printout by if the output is a terminal
            suppressPrintout = not sys.stdout.isatty()
        self.disable_colors = disable_colors
        self.callerStackDepth = callerStackDepth
        if not programName:
            programName , _ = getCallerInfo(i=self.callerStackDepth)
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
        if in_place_compression:
            if in_place_compression in ['gzip', 'bz2', 'xz', 'lzma', 'zstd', 'zst']:
                in_place_compression = in_place_compression
                if in_place_compression == 'zst':
                    in_place_compression = 'zstd'
                if in_place_compression == 'zstd':
                    try:
                        from compression import zstd
                        _ = zstd
                    except ImportError:
                        printWithColor(
                            'compression.zstd module not available in this Python build, using xz instead',
                            'warning',
                            disable_colors=self.disable_colors,
                        )
                        in_place_compression = 'xz'
            elif in_place_compression is ... or in_place_compression is True:
                in_place_compression = 'xz'
            else:
                printWithColor(f'Invalid in_place_compression {in_place_compression}, using xz instead', 'warning',disable_colors=self.disable_colors)
                in_place_compression = 'xz'

        else:
            in_place_compression = None
        self.in_place_compression = in_place_compression
        if collapse_single_day_logs is ...:
            if self.in_place_compression:
                collapse_single_day_logs = True
            else:
                collapse_single_day_logs = False
        self.collapse_single_day_logs = collapse_single_day_logs
        self.version = version
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self._clear_file_handlers()
        if systemLogFileDir in ['/dev/null', '/dev/stdout', '/dev/stderr']:
            self.noLog = True
        if not self.noLog:
            self.systemLogFileDir = os.path.abspath(systemLogFileDir)
            self.logsDir = os.path.join(self.systemLogFileDir, self.name + '_log')
            self.logFileDir = os.path.join(self.logsDir, self.currentDateTime.partition("_")[0])
            if self.collapse_single_day_logs:
                self.logFileName = os.path.join(self.logFileDir, self.name + '_' + self.currentDateTime.partition("_")[0] + '.log')
            else:
                self.logFileName = os.path.join(self.logFileDir, self.name + '_' + self.currentDateTime + '.log')
            try:
                self._setup_file_logging(programName, binary_mode, compression_level)
            except Exception as e:
                printWithColor(e, 'error', disable_colors=self.disable_colors)
                printWithColor(
                    'Failed to create log file! Trying to write to /tmp',
                    'error',
                    disable_colors=self.disable_colors,
                )
                try:
                    self._clear_file_handlers()
                    self.systemLogFileDir = '/tmp'
                    self.logsDir = os.path.join(self.systemLogFileDir, self.name + '_log')
                    self.logFileDir = os.path.join(self.logsDir, self.currentDateTime.partition("_")[0])
                    if self.collapse_single_day_logs:
                        self.logFileName = os.path.join(
                            self.logFileDir,
                            self.name + '_' + self.currentDateTime.partition("_")[0] + '.log',
                        )
                    else:
                        self.logFileName = os.path.join(
                            self.logFileDir, self.name + '_' + self.currentDateTime + '.log',
                        )
                    self._setup_file_logging(programName, binary_mode, compression_level)
                except Exception as e:
                    printWithColor(e, 'error', disable_colors=self.disable_colors)
                    printWithColor('Failed to create log file in /tmp', 'error', disable_colors=self.disable_colors)
                    handler = logging.StreamHandler()
                    formatter = logging.Formatter(
                        '%(asctime)s [%(levelname)s] [%(callerFileLocation)s] %(message)s',
                    )
                    handler.setFormatter(formatter)
                    self.logger.addHandler(handler)
                    printWithColor('Log file: sys.stderr', 'info', disable_colors=self.disable_colors)
            self.info(f'>>>>>>>>>>>>>>>>>>>Starting {programName} at {self.currentDateTime}<<<<<<<<<<<<<<<<<')
        else:
            self.systemLogFileDir = '/dev/null'
            self.logsDir = '/dev/null'
            self.logFileDir = '/dev/null'
            self.logFileName = '/dev/null'
            self.logger.addHandler(logging.NullHandler())

    def _clear_file_handlers(self):
        for handler in list(self.logger.handlers):
            if isinstance(handler, logging.FileHandler):
                self.logger.removeHandler(handler)
                handler.close()

    def _make_log_handler(self, binary_mode, compression_level):
        compressed_latest_log_name = None
        if self.in_place_compression == 'gzip':
            self.logFileName += '.gz'
            compressed_latest_log_name = '.gz'
            handler = self.GZipFileHandler(
                self.logFileName, encoding=self.encoding, mode='ab' if binary_mode else 'a',
            )
            if compression_level is not ...:
                handler.compresslevel = compression_level
        elif self.in_place_compression == 'bz2':
            self.logFileName += '.bz2'
            compressed_latest_log_name = '.bz2'
            handler = self.BZ2FileHandler(
                self.logFileName, encoding=self.encoding, mode='ab' if binary_mode else 'a',
            )
            if compression_level is not ...:
                handler.compresslevel = compression_level
        elif self.in_place_compression in ('xz', 'lzma'):
            self.logFileName += '.xz'
            compressed_latest_log_name = '.xz'
            handler = self.XZFileHandler(
                self.logFileName, encoding=self.encoding, mode='ab' if binary_mode else 'a',
            )
            if compression_level is not ...:
                handler.preset = compression_level
        elif self.in_place_compression == 'zstd':
            self.logFileName += '.zst'
            compressed_latest_log_name = '.zst'
            handler = self.ZSTDFileHandler(
                self.logFileName, encoding=self.encoding, mode='ab' if binary_mode else 'a',
            )
            if compression_level is not ...:
                handler.level = compression_level
        else:
            handler = self.BinFileHandler(
                self.logFileName, encoding=self.encoding, mode='ab' if binary_mode else 'a',
            )
        return handler, compressed_latest_log_name

    def _link_latest_log(self, latest_log_name, compressed_suffix=None):
        if os.name == 'nt':
            return
        if compressed_suffix:
            latest_log_name = latest_log_name + compressed_suffix
        if os.path.islink(latest_log_name):
            os.unlink(latest_log_name)
        if os.path.exists(latest_log_name):
            os.remove(latest_log_name)
        os.symlink(os.path.relpath(self.logFileName, self.logsDir), latest_log_name)

    def _setup_file_logging(self, programName, binary_mode, compression_level):
        latest_log_name = os.path.join(self.logsDir, programName + '_latest.log')
        if not os.path.exists(self.logFileDir):
            os.makedirs(self.logFileDir)
        handler, compressed_suffix = self._make_log_handler(binary_mode, compression_level)
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)-8s] [%(callerFileLocation)s] %(message)s',
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self._link_latest_log(latest_log_name, compressed_suffix)
        printWithColor('Log file: ' + self.logFileName, 'info', disable_colors=self.disable_colors)
        self.cleanup_old_logs()

    def cleanup_old_logs(self):
        """Compress or delete day-folders under ``logsDir`` based on age settings."""
        if self.noLog:
            return
        if not os.path.isdir(self.logsDir):
            return
        pending_tasks = []
        for dirName in os.listdir(self.logsDir):
            dir_key = _log_dir_date_key(dirName)
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', dir_key):
                continue
            currentPath = os.path.join(self.logsDir, dirName)
            if not os.access(currentPath, os.W_OK):
                self.info(f'Skipping {currentPath} as it is not writable')
                continue
            try:
                dirTime = dateutil.parser.parse(dir_key).timestamp()
            except Exception:
                try:
                    mtime = os.path.getmtime(currentPath)
                    ctime = os.path.getctime(currentPath)
                    dirTime = max(mtime, ctime)
                except Exception:
                    printWithColor(
                        f'Failed to get the creation time for {dirName}, skipping',
                        'error',
                        disable_colors=self.disable_colors,
                    )
                    continue
            if self.deleteLogAfterYears != 0 and datetime.datetime.now().timestamp() - dirTime > self.deleteLogAfterYears * 365 * 24 * 3600:
                self.teelog(f'Deleting log dir {dirName} as it is older than {self.deleteLogAfterYears} years', 'info')
                remove_function = shutil.rmtree if os.path.isdir(currentPath) else os.remove
                pending_tasks.append((remove_function, (currentPath,), {}))
            elif self.compressLogAfterMonths != 0 and not dirName.endswith('.tar.xz') and datetime.datetime.now().timestamp() - dirTime > self.compressLogAfterMonths * 30 * 24 * 3600:
                self.teelog(f'Compressing log dir {dirName} as it is older than {self.compressLogAfterMonths} months', 'info')
                pending_tasks.append((
                    compress_folder,
                    (currentPath,),
                    {'disable_colors': self.disable_colors},
                ))
        if not pending_tasks:
            return
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=1) as executor:
            for func, args, kwargs in pending_tasks:
                executor.submit(func, *args, **kwargs)


    def log_with_caller_info(self, level, msg, callerStackDepth=...):
        """Write ``msg`` at ``level`` with abbreviated caller file/line metadata."""
        if self.noLog:
            return
        if callerStackDepth == ...:
            callerStackDepth = self.callerStackDepth
        filename, lineno = getCallerInfo(i=callerStackDepth)
        extra = {'callerFileLocation': abbreviate_filename(filename, lineno, target_length=self.fileDescriptorLength)}
        logger = self.logger
        if level == 'info':
            logger.info(msg, extra=extra)
        elif level == 'debug':
            logger.debug(msg, extra=extra)
        elif level == 'warning':
            logger.warning(msg, extra=extra)
        elif level == 'error':
            logger.error(msg, extra=extra)
        elif level == 'critical':
            logger.critical(msg, extra=extra)
        else:
            logger.info(msg, extra=extra)

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
        """Log ``msg`` at info level (file only unless ``suppressPrintout`` is False)."""
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def teeerror(self, msg, callerStackDepth=...):
        """Print ``msg`` as an error and log it at error level."""
        if not self.suppressPrintout:
            printWithColor(msg, 'error',disable_colors=self.disable_colors)
        self.log_with_caller_info('error', msg, callerStackDepth=callerStackDepth)

    def error(self, msg, callerStackDepth=...):
        """Log ``msg`` at error level without printing to stdout."""
        self.log_with_caller_info('error', msg, callerStackDepth=callerStackDepth)

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
    parser.add_argument('--test', action='store_true', help='run doctests')
    args = parser.parse_args()
    if args.test:
        results = doctest.testmod(verbose='-v')
        raise SystemExit(1 if results.failed else 0)
    print(f'Tee_Logger {version} by {__author__}')