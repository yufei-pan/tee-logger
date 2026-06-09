#!/usr/bin/env python3
import logging
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from Tee_Logger import (
    _log_dir_date_key,
    getCallerInfo,
    teeLogger,
)


def _latest_log_content(program_name):
    log_root = f'/tmp/{program_name}_log'
    log_path = sorted(
        os.path.join(log_root, d)
        for d in os.listdir(log_root)
        if os.path.isdir(os.path.join(log_root, d))
    )[-1]
    log_file = sorted(f for f in os.listdir(log_path) if f.endswith('.log'))[-1]
    with open(os.path.join(log_path, log_file)) as fh:
        return fh.read()


class TestLogDirDateKey(unittest.TestCase):
    def test_plain_date(self):
        self.assertEqual(_log_dir_date_key('2020-01-01'), '2020-01-01')

    def test_tar_xz_suffix(self):
        self.assertEqual(_log_dir_date_key('2020-01-01.tar.xz'), '2020-01-01')

    def test_txt_not_stripped(self):
        self.assertEqual(_log_dir_date_key('2020-01-01.txt'), '2020-01-01.txt')
        self.assertIsNone(re.match(r'^\d{4}-\d{2}-\d{2}$', _log_dir_date_key('2020-01-01.txt')))


class TestGetCallerInfo(unittest.TestCase):
    def test_auto_mode_from_user_wrapper(self):
        tl = teeLogger(programName='caller_auto', systemLogFileDir='/tmp', suppressPrintout=True)

        def my_wrapper(msg):
            log_at = sys._getframe().f_lineno + 1
            tl.info(msg)
            return log_at

        def business():
            return my_wrapper('wrapped message')

        log_at = business()
        content = _latest_log_content('caller_auto')
        self.assertIn(f':{log_at}', content.split('wrapped message')[0])
        self.assertNotIn('Tee_Logger', content.split('wrapped message')[0].split('\n')[-1])

    def test_explicit_positive_depth(self):
        filename, _lineno = getCallerInfo(1)
        self.assertEqual(filename, os.path.basename(__file__))

    def test_auto_mode_direct_call(self):
        tl = teeLogger(programName='caller_direct', systemLogFileDir='/tmp', suppressPrintout=True)
        log_at = sys._getframe().f_lineno + 1
        tl.info('direct message')
        content = _latest_log_content('caller_direct')
        self.assertIn('direct message', content)
        self.assertIn(f':{log_at}', content)


class TestDuplicateHandlers(unittest.TestCase):
    def test_same_program_name_replaces_file_handler(self):
        tl1 = teeLogger(programName='dup_handlers', systemLogFileDir='/tmp', suppressPrintout=True)
        tl2 = teeLogger(programName='dup_handlers', systemLogFileDir='/tmp', suppressPrintout=True)
        logger = logging.getLogger('dup_handlers')
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        self.assertEqual(len(file_handlers), 1)
        tl1.info('once')
        tl2.info('twice')
        content = _latest_log_content('dup_handlers')
        self.assertEqual(content.count('once'), 1)
        self.assertEqual(content.count('twice'), 1)


class TestGzipBinaryMode(unittest.TestCase):
    def test_gzip_respects_binary_mode_false(self):
        tl = teeLogger(
            programName='gzip_text',
            systemLogFileDir='/tmp',
            in_place_compression='gzip',
            binary_mode=False,
            suppressPrintout=True,
        )
        handler = tl.logger.handlers[0]
        self.assertEqual(handler.mode, 'at')


if __name__ == '__main__':
    unittest.main()
