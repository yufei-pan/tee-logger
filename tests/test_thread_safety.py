"""Regression tests for output from several threads or a signal handler at once."""
import sys

import pytest

import Tee_Logger
from Tee_Logger import printWithColor


class InterruptingStream:
    """A write-through stream that runs ``interrupt`` once, right after its first write().

    That is where another thread (or a signal handler on this one) could get to
    write to the same stream, so it forces the worst-case interleaving without
    any timing dependence.
    """

    def __init__(self, interrupt=None):
        self.writes = []
        self.interrupt = interrupt

    def write(self, data):
        self.writes.append(data)
        interrupt, self.interrupt = self.interrupt, None
        if interrupt is not None:
            interrupt()
        return len(data)

    def flush(self):
        pass

    def isatty(self):
        return False

    def getvalue(self):
        return "".join(self.writes)


@pytest.mark.parametrize("disable_colors", [True, False])
def test_print_with_color_writes_each_line_once(disable_colors):
    stream = InterruptingStream()
    stream.interrupt = lambda: printWithColor("second", "error", disable_colors=disable_colors, file=stream)
    printWithColor("first", "warning", disable_colors=disable_colors, file=stream)
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2, stream.writes
    assert lines[0].endswith("first" + ("" if disable_colors else Tee_Logger.bcolors.ENDC))
    assert lines[1].endswith("second" + ("" if disable_colors else Tee_Logger.bcolors.ENDC))
    assert all(w.endswith("\n") and w.count("\n") == 1 for w in stream.writes)


def test_diagnostics_and_console_output_are_single_writes(tmp_path, monkeypatch):
    from log_utils import close_tee_logger, make_tee_logger

    tl = make_tee_logger(tmp_path, suppressPrintout=False)
    err = InterruptingStream()
    out = InterruptingStream()
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "stdout", out)
    # the console line (main thread) and a diagnostic (e.g. from the
    # teeLogger-cleanup thread) each interrupted by another writer
    out.interrupt = lambda: out.write("other thread\n")
    err.interrupt = lambda: err.write("other thread\n")
    tl.teeprint("console line")
    tl._diagnose("cleanup failed")
    close_tee_logger(tl)
    assert out.getvalue() == "[INFO] console line\nother thread\n"
    assert err.getvalue() == "[ERROR] cleanup failed\nother thread\n"


def test_print_with_color_without_stdout(monkeypatch):
    # print() is a no-op when sys.stdout is None (pythonw); so is printWithColor
    monkeypatch.setattr(sys, "stdout", None)
    printWithColor("nowhere", "info", disable_colors=True)
