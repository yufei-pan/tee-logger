#!/usr/bin/env python3
import doctest
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import Tee_Logger


class TestDoctestModule(unittest.TestCase):
    def test_tee_logger_doctests(self):
        results = doctest.testmod(Tee_Logger, verbose=False)
        self.assertEqual(results.failed, 0)


if __name__ == '__main__':
    unittest.main()
