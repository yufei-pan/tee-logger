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
    assert pretty_format_table([]) == ""


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
