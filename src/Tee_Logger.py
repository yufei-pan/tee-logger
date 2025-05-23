#!/usr/bin/env python3
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
except:
    pass

version = '6.37'
__version__ = version

__author__ = 'Yufei Pan (pan@zopyr.us)'

class bcolors:
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
    def base64_to_int(b64_str):
        # Decode the base64 string to bytes
        byte_array = base64.b64decode(b64_str)
        # Convert the bytes to an integer and return it
        return int.from_bytes(byte_array, byteorder='big')
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
    version = 1.11
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


# def getCallerInfo(i=2):
#     try:
#         inspect_stack_size = len(inspect.stack())
#         if i < -inspect_stack_size:
#             i = -inspect_stack_size
#         if i >= inspect_stack_size:
#             i = inspect_stack_size -1
#         frame = inspect.stack()[i]
#         filename = os.path.basename(frame.filename)
#         lineno = frame.lineno
#     except Exception as e:
#         filename = f'TLError {e}'
#         lineno = 0
#     return filename, lineno

# def getCallerInfo(i=2):
#     try:
#         frame = sys._getframe(i)
#         return os.path.basename(frame.f_code.co_filename), frame.f_lineno
#     except ValueError:
#         return 'TLError ValueError', 0
#     except Exception as e:
#         return f'TLError {e}', 0
def getCallerInfo(i=2):
    try:
        if i < 0:
            frame = inspect.currentframe()
            frames = []
            while frame:
                frames.append(frame)
                frame = frame.f_back
            total_frames = len(frames)
            while i < 0:
                i += total_frames
            frame = frames[i]
        else:
            frame = inspect.currentframe()
            for _ in range(i):
                if frame.f_back is None:
                    break
                frame = frame.f_back
        filename = os.path.basename(frame.f_code.co_filename)
        lineno = frame.f_lineno
    except Exception as e:
        filename = f'TLError {e}'
        lineno = 0
    finally:
        del frame  # explicitly delete to avoid reference cycles
    return filename, lineno

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
    class GZipFileHandler(logging.FileHandler):
        """Logging handler that writes directly to a gzip compressed file"""
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
        """Logging handler that writes directly to a bzip2 compressed file"""
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
        """Logging handler that writes directly to a lzma compressed file"""
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
    
    class BinFileHandler(logging.FileHandler):
        """Logging handler that writes directly to a binary file"""
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
            if in_place_compression in ['gzip', 'bz2', 'xz', 'lzma']:
                in_place_compression = in_place_compression
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
            latest_log_name = os.path.join(self.logsDir, programName + '_latest.log')
            compressed_latest_log_name = None
            try:
                if not os.path.exists(self.logFileDir):
                    os.makedirs(self.logFileDir)
                if self.in_place_compression:
                    if self.in_place_compression == 'gzip':
                        self.logFileName += '.gz'
                        compressed_latest_log_name = latest_log_name + '.gz'
                        handler = self.GZipFileHandler(self.logFileName,encoding=self.encoding,mode='ab')
                        if compression_level is not ...:
                            handler.compresslevel = compression_level
                    elif self.in_place_compression == 'bz2':
                        self.logFileName += '.bz2'
                        compressed_latest_log_name = latest_log_name + '.bz2'
                        handler = self.BZ2FileHandler(self.logFileName,encoding=self.encoding,mode='ab' if binary_mode else 'a')
                        if compression_level is not ...:
                            handler.compresslevel = compression_level
                    elif self.in_place_compression == 'xz' or self.in_place_compression == 'lzma':
                        self.logFileName += '.xz'
                        compressed_latest_log_name = latest_log_name + '.xz'
                        handler = self.XZFileHandler(self.logFileName,encoding=self.encoding,mode='ab' if binary_mode else 'a')
                        if compression_level is not ...:
                            handler.preset = compression_level
                else:
                    handler = self.BinFileHandler(self.logFileName, encoding=self.encoding,mode='ab' if binary_mode else 'a')
                formatter = logging.Formatter('%(asctime)s [%(levelname)-8s] [%(callerFileLocation)s] %(message)s')
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)
                # also link the log file to the logsDir/programName_latest.log if not on windows
                if os.name != 'nt':
                    if os.path.islink(latest_log_name):
                        os.unlink(latest_log_name)
                    if os.path.exists(latest_log_name):
                        os.remove(latest_log_name)
                    if compressed_latest_log_name:
                        latest_log_name = compressed_latest_log_name
                    if os.path.islink(latest_log_name):
                        os.unlink(latest_log_name)
                    if os.path.exists(latest_log_name):
                        os.remove(latest_log_name)
                    # create a relative path symlink
                    os.symlink(os.path.relpath(self.logFileName, self.logsDir), latest_log_name)
                printWithColor('Log file: ' + self.logFileName, 'info',disable_colors=self.disable_colors)
                self.cleanup_old_logs()
                #self.info(f'>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>\nStarting {programName} at {self.currentDateTime}')
            except Exception as e:
                printWithColor(e, 'error',disable_colors=self.disable_colors)
                printWithColor('Failed to create log file! Trying to write to /tmp', 'error', disable_colors=self.disable_colors)
                try:
                    self.systemLogFileDir = '/tmp'
                    self.logsDir = os.path.join(self.systemLogFileDir, self.name + '_log')
                    self.logFileDir = os.path.join(self.logsDir, self.currentDateTime.partition("_")[0])
                    if self.collapse_single_day_logs:
                        self.logFileName = os.path.join(self.logFileDir, self.name + '_' + self.currentDateTime.partition("_")[0] + '.log')
                    else:
                        self.logFileName = os.path.join(self.logFileDir, self.name + '_' + self.currentDateTime + '.log')
                    if not os.path.exists(self.logFileDir):
                        os.makedirs(self.logFileDir)
                    handler = logging.FileHandler(self.logFileName, encoding=self.encoding)
                    formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(callerFileLocation)s] %(message)s')
                    handler.setFormatter(formatter)
                    self.logger.addHandler(handler)
                    latest_log_name = os.path.join(self.logsDir, programName + '_latest.log')
                    if os.path.islink(latest_log_name):
                        os.unlink(latest_log_name)
                    if os.path.exists(latest_log_name):
                        os.remove(latest_log_name)
                    # create a relative path symlink
                    os.symlink(os.path.relpath(self.logFileName, self.logsDir), latest_log_name)
                    printWithColor('Log file: ' + self.logFileName, 'info',disable_colors=self.disable_colors)
                    #self.info(f'>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>\nStarting {programName} at {self.currentDateTime}')
                except Exception as e:
                    printWithColor(e, 'error',disable_colors=self.disable_colors)
                    printWithColor('Failed to create log file in /tmp', 'error',disable_colors=self.disable_colors)
                    # creating a stream handler to print to stdout
                    handler = logging.StreamHandler()
                    formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(callerFileLocation)s] %(message)s')
                    handler.setFormatter(formatter)
                    self.logger.addHandler(handler)
                    printWithColor('Log file: sys.stderr', 'info',disable_colors=self.disable_colors)
            self.info(f'>>>>>>>>>>>>>>>>>>>Starting {programName} at {self.currentDateTime}<<<<<<<<<<<<<<<<<')
        else:
            self.systemLogFileDir = '/dev/null'
            self.logsDir = '/dev/null'
            self.logFileDir = '/dev/null'
            self.logFileName = '/dev/null'
            self.logger.addHandler(logging.NullHandler())




    def compress_folder(self, folderPath):
        # if on linux and tar and xz is available, use them to compress the folder
        if os.name != 'nt' and shutil.which('tar') and shutil.which('xz'):
            try:
                relativePath = os.path.basename(folderPath)
                subprocess.run(['tar', '-caf', folderPath + '.tar.xz', '--remove-files', relativePath], cwd=os.path.dirname(folderPath))
                return True
            except Exception as e:
                printWithColor(f'Failed to compress folder {relativePath} with tar due to {e}', 'error',disable_colors=self.disable_colors)
                printWithColor('Falling back to python implementation', 'warning',disable_colors=self.disable_colors)
        try:
            with tarfile.open(folderPath + '.tar.xz', 'w:xz') as tar:
                tar.add(folderPath, arcname=os.path.basename(folderPath))
            shutil.rmtree(folderPath)
            return True
        except Exception as e:
            printWithColor(f'Failed to compress folder due to {e}', 'error',disable_colors=self.disable_colors)
            return False

    def cleanup_old_logs(self):
        if self.noLog:
            return
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=1) as executor:
            futures = []
            for dirName in os.listdir(self.logsDir):
                # Skip the folder it it is not in the format YYYY-MM-DD
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', dirName.rstrip('.tar.xz')):
                    continue
                currentPath = os.path.join(self.logsDir, dirName)
                # skip the path if it is not writable
                if not os.access(currentPath, os.W_OK):
                    self.info(f'Skipping {currentPath} as it is not writable')
                    continue
                try:
                    dirTime = dateutil.parser.parse(dirName.rstrip('.tar.xz')).timestamp()
                except Exception as e:
                    try:
                        mtime = os.path.getmtime(currentPath)
                        ctime = os.path.getctime(currentPath)
                        dirTime = max(mtime, ctime)
                    except Exception as e:
                        printWithColor(f'Failed to get the creation time for {dirName}, skipping', 'error',disable_colors=self.disable_colors)
                        continue
                if self.deleteLogAfterYears != 0 and datetime.datetime.now().timestamp() - dirTime > self.deleteLogAfterYears * 365 * 24 * 3600:
                    self.teelog(f'Deleting log dir {dirName} as it is older than {self.deleteLogAfterYears} years', 'info')
                    # if os.name != 'nt':
                    #     subprocess.run(['rm', '-rf', currentPath])
                    # else:
                    #     subprocess.run(['rmdir', '/s', '/q', currentPath])
                    #shutil.rmtree(currentPath)
                    remove_function = shutil.rmtree if os.path.isdir(currentPath) else os.remove
                    futures.append(executor.submit(remove_function, currentPath))
                elif self.compressLogAfterMonths != 0 and not dirName.endswith('.tar.xz') and datetime.datetime.now().timestamp() - dirTime > self.compressLogAfterMonths * 30 * 24 * 3600:
                    self.teelog(f'Compressing log dir {dirName} as it is older than {self.compressLogAfterMonths} months', 'info')
                    # if os.name != 'nt':
                    #     subprocess.run(['tar', '-caf', os.path.join(self.logsDir, dirName + ".tar.xz"), '--remove-files', currentPath])
                    # else:
                    #     subprocess.run(['tar', '-caf', os.path.join(self.logsDir, dirName + ".tar.xz"), currentPath])
                    # create a seperate process to compress the log file
                    futures.append(executor.submit(self.compress_folder, currentPath))


    def log_with_caller_info(self, level, msg, callerStackDepth=...):
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
        if not self.suppressPrintout:
            printWithColor(msg, 'okgreen',disable_colors=self.disable_colors)
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def printTable(self, data, callerStackDepth=..., header=None):
        tableStr = pretty_format_table(data, header=header)
        if not self.suppressPrintout:
            printWithColor(tableStr, 'info',disable_colors=self.disable_colors)
        self.log_with_caller_info('info', msg='\n' + tableStr, callerStackDepth=callerStackDepth)

    def ok(self, msg, callerStackDepth=...):
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def teeprint(self, msg, callerStackDepth=...):
        if not self.suppressPrintout:
            printWithColor(msg, 'info',disable_colors=self.disable_colors)
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def info(self, msg, callerStackDepth=...):
        self.log_with_caller_info('info', msg=msg, callerStackDepth=callerStackDepth)

    def teeerror(self, msg, callerStackDepth=...):
        if not self.suppressPrintout:
            printWithColor(msg, 'error',disable_colors=self.disable_colors)
        self.log_with_caller_info('error', msg, callerStackDepth=callerStackDepth)

    def error(self, msg, callerStackDepth=...):
        self.log_with_caller_info('error', msg, callerStackDepth=callerStackDepth)

    def teelog(self, msg, level, callerStackDepth=...):
        if not self.suppressPrintout:
            printWithColor(msg, level,disable_colors=self.disable_colors)
        self.log_with_caller_info(level, msg, callerStackDepth=callerStackDepth)


    def log(self, msg, level, callerStackDepth=...):
        self.log_with_caller_info(level, msg, callerStackDepth=callerStackDepth)

if __name__ == '__main__':
    print(f'Tee_Logger {version} by {__author__}')