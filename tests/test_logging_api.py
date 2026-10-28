import re

import pytest

from log_utils import find_log_files, read_log_text


@pytest.mark.parametrize(
    "method,msg,level_token",
    [
        ("info", "m-info", "INFO"),
        ("error", "m-error", "ERROR"),
        ("ok", "m-ok", "INFO"),
        ("debug", "m-debug", "DEBUG"),
        ("warning", "m-warning", "WARNING"),
        ("critical", "m-critical", "CRITICAL"),
    ],
)
def test_file_only_methods(tee_factory, tmp_path, capsys, method, msg, level_token):
    tl = tee_factory(programName="api_file", suppressPrintout=False)
    getattr(tl, method)(msg)
    captured = capsys.readouterr()
    assert msg not in captured.out
    content = read_log_text(find_log_files(tmp_path / "api_file_log")[-1])
    line = [ln for ln in content.splitlines() if msg in ln][-1]
    assert "[" + level_token in line


@pytest.mark.parametrize(
    "method,msg",
    [
        ("teeprint", "t-print"),
        ("teeerror", "t-error"),
        ("teeok", "t-ok"),
        ("teedebug", "t-debug"),
        ("teewarning", "t-warning"),
        ("teecritical", "t-critical"),
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


def test_a_broken_console_does_not_lose_the_file_record(tee_factory, tmp_path, monkeypatch):
    # e.g. `prog | head -1` once head has exited
    import sys

    class BrokenPipe:
        def write(self, data):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            pass

        def isatty(self):
            return False

    tl = tee_factory(programName="api_broken", suppressPrintout=False, disable_colors=True)
    monkeypatch.setattr(sys, "stdout", BrokenPipe())
    with pytest.raises(BrokenPipeError):
        tl.teeprint("still in the file")
    monkeypatch.undo()
    assert "still in the file" in read_log_text(find_log_files(tmp_path / "api_broken_log")[-1])


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


@pytest.mark.parametrize("method", ["exception", "teeexception"])
def test_exception_logs_traceback_to_file(tee_factory, tmp_path, capsys, method):
    tl = tee_factory(programName="api_exc", suppressPrintout=False, disable_colors=True)
    try:
        raise ValueError("bad value 42")
    except ValueError:
        getattr(tl, method)("while parsing")
    out = capsys.readouterr().out
    content = read_log_text(find_log_files(tmp_path / "api_exc_log")[-1])
    assert "[ERROR" in content
    assert "while parsing" in content
    assert "Traceback (most recent call last)" in content
    assert "ValueError: bad value 42" in content
    if method == "teeexception":
        assert "while parsing" in out and "ValueError: bad value 42" in out
        assert "Traceback" not in out
    else:
        assert out == ""
