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


def _call_with_deadline(fn, *args, timeout=5.0):
    """Run fn in a daemon thread so an infinite loop fails the test instead of hanging the suite."""
    import threading

    result = {}
    t = threading.Thread(target=lambda: result.setdefault("value", fn(*args)), daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), f"{fn.__name__}{args} did not return within {timeout}s"
    return result["value"]


@pytest.mark.parametrize(
    "filename,line,expected",
    [
        ("worker_process_1.py", 42, "WorkerP1:42    "),
        ("data_pipeline_stage_2.py", 42, "DataPS2:42     "),
        ("1234567890123456.py", 42, "123456789012:42"),
    ],
)
def test_abbreviate_filename_terminates_on_trailing_numbers(filename, line, expected):
    assert _call_with_deadline(abbreviate_filename, filename, line) == expected


def test_abbreviate_filename_keeps_numbered_scripts_distinct():
    one = _call_with_deadline(abbreviate_filename, "worker_process_1.py", 1234)
    two = _call_with_deadline(abbreviate_filename, "worker_process_2.py", 1234)
    assert one != two


def test_pretty_format_table_header_shorter_than_rows_is_padded():
    out = pretty_format_table([["x", "y"], ["1", "2"]], header=["a"])
    assert out == "a |  \n--+--\nx | y\n1 | 2\n"


def test_pretty_format_table_string_header_sets_widths():
    out = pretty_format_table([["x", "y"]], header="longname\tvalue2")
    assert out == "longname | value2\n---------+-------\nx        | y     \n"


def test_pretty_format_table_does_not_mutate_header():
    header = ["a"]
    pretty_format_table([["x", "y"]], header=header)
    assert header == ["a"]


def test_pretty_format_table_list_of_dicts_aligns_by_key():
    out = pretty_format_table([{"a": 1, "b": 2}, {"b": 3, "a": 4}])
    assert out == "a | b\n--+--\n1 | 2\n4 | 3\n"


def test_pretty_format_table_dict_of_dicts_aligns_by_key():
    out = pretty_format_table({"r1": {"a": 1, "b": 2}, "r2": {"b": 3, "a": 4}})
    assert out == "key | a | b\n----+---+--\nr1  | 1 | 2\nr2  | 4 | 3\n"


def test_pretty_format_table_ragged_rows_are_padded():
    out = pretty_format_table([["h1", "h2", "h3"], ["a"], ["b", "c"]])
    assert out == "h1 | h2 | h3\n---+----+---\na  |    |   \nb  | c  |   \n"


def test_print_with_color_error_is_red():
    buf = io.StringIO()
    with redirect_stdout(buf):
        printWithColor("boom", "error", disable_colors=False)
    assert buf.getvalue() == "\033[91mboom\033[0m\n"


def test_print_with_color_auto_disables_colors_when_not_a_tty():
    buf = io.StringIO()
    with redirect_stdout(buf):
        printWithColor("hi", "warning")
    assert buf.getvalue() == "[WARNING] hi\n"


def test_print_with_color_writes_to_given_stream(capsys):
    buf = io.StringIO()
    printWithColor("to-buf", "info", disable_colors=True, file=buf)
    assert buf.getvalue() == "[INFO] to-buf\n"
    assert capsys.readouterr().out == ""
