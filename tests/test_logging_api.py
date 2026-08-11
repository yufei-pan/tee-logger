import re

import pytest

from log_utils import find_log_files, read_log_text


@pytest.mark.parametrize(
    "method,msg,level_token",
    [
        ("info", "m-info", "INFO"),
        ("error", "m-error", "ERROR"),
        ("ok", "m-ok", "INFO"),
    ],
)
def test_file_only_methods(tee_factory, tmp_path, capsys, method, msg, level_token):
    tl = tee_factory(programName="api_file", suppressPrintout=False)
    getattr(tl, method)(msg)
    captured = capsys.readouterr()
    assert msg not in captured.out
    content = read_log_text(find_log_files(tmp_path / "api_file_log")[-1])
    assert msg in content
    assert level_token in content


@pytest.mark.parametrize(
    "method,msg",
    [
        ("teeprint", "t-print"),
        ("teeerror", "t-error"),
        ("teeok", "t-ok"),
    ],
)
def test_tee_methods_hit_stdout_and_file(tee_factory, tmp_path, capsys, method, msg):
    tl = tee_factory(programName="api_tee", suppressPrintout=False, disable_colors=True)
    getattr(tl, method)(msg)
    captured = capsys.readouterr()
    assert msg in captured.out
    content = read_log_text(find_log_files(tmp_path / "api_tee_log")[-1])
    assert msg in content


def test_log_and_teelog_levels(tee_factory, tmp_path, capsys):
    tl = tee_factory(programName="api_levels", suppressPrintout=False, disable_colors=True)
    tl.log("plain-warn", "warning")
    tl.teelog("tee-warn", "warning")
    captured = capsys.readouterr()
    assert "plain-warn" not in captured.out
    assert "tee-warn" in captured.out
    content = read_log_text(find_log_files(tmp_path / "api_levels_log")[-1])
    assert "plain-warn" in content and "tee-warn" in content


def test_print_table(tee_factory, tmp_path):
    tl = tee_factory(programName="api_table")
    tl.printTable([["h1", "h2"], ["a", "b"]])
    content = read_log_text(find_log_files(tmp_path / "api_table_log")[-1])
    assert "a" in content and "b" in content


def test_log_line_shape(tee_factory, tmp_path):
    tl = tee_factory(programName="api_shape")
    tl.info("shape-msg")
    content = read_log_text(find_log_files(tmp_path / "api_shape_log")[-1])
    line = [ln for ln in content.splitlines() if "shape-msg" in ln][-1]
    assert re.search(r"\[\w+\s*\]", line)
    assert "shape-msg" in line
