"""Regression tests for output from several threads or a signal handler at once."""
import decimal
import errno
import fractions
import sys
import threading
import time
from pathlib import Path

import pytest

import Tee_Logger
from Tee_Logger import printWithColor
from log_utils import close_tee_logger, make_tee_logger, read_log_text, salvage_log_text


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


class FlakyStream:
    """Wraps a compressed log stream; its first ``failures`` flush()/close() calls raise ENOSPC."""

    def __init__(self, real, failures):
        self.real = real
        self.failures = failures
        self.attempts = 0
        self.attempted = threading.Event()

    def write(self, data):
        return self.real.write(data)

    def _attempt(self, action):
        self.attempts += 1
        try:
            if self.failures > 0:
                self.failures -= 1
                if action == "close":
                    self.real.close()  # a failed close still closes, as the real streams do
                raise OSError(errno.ENOSPC, "No space left on device")
            getattr(self.real, action)()
        finally:
            self.attempted.set()

    def flush(self):
        self._attempt("flush")

    def close(self):
        self._attempt("close")


def _compressed_handler(tl):
    (handler,) = [h for h in tl.logger.handlers if isinstance(h, Tee_Logger._CompressedFileHandler)]
    return handler


def _install(handler, stream):
    """Make ``stream`` the handler's dirty stream and make sure a flusher is running."""
    handler.acquire()
    try:
        old = handler.stream
        handler.stream = stream
        handler._dirty = True
        if handler._flusher_stop is None:
            handler._start_flusher()
    finally:
        handler.release()
    return old


def _record_errors(handler):
    errors = []
    handler.handleError = lambda record: errors.append((record, sys.exc_info()[1]))
    return errors


def test_flusher_survives_flush_errors_and_reports_once(tmp_path):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=0.05)
    handler = _compressed_handler(tl)
    errors = _record_errors(handler)
    handler.acquire()
    try:
        stream = FlakyStream(handler.stream, failures=3)
    finally:
        handler.release()
    _install(handler, stream)
    deadline = time.monotonic() + 10
    while stream.attempts < 4 and time.monotonic() < deadline:
        stream.attempted.wait(0.1)
        stream.attempted.clear()
    handler.acquire()
    try:
        attempts, dirty = stream.attempts, handler._dirty
    finally:
        handler.release()
    tl.warning("after the disk freed up")
    path = Path(tl.logFileName)
    close_tee_logger(tl)
    # the flusher kept retrying after the first failure instead of dying with it
    assert attempts >= 4, f"flusher stopped after {attempts} flush attempt(s)"
    assert not dirty
    # one report for the whole run of failures, not one per tick
    assert len(errors) == 1, errors
    assert isinstance(errors[0][1], OSError) and errors[0][1].errno == errno.ENOSPC
    assert "after the disk freed up" in read_log_text(path)


def test_flusher_survives_a_failed_stream_finish(tmp_path):
    # xz cannot flush mid-stream, so a flush closes the stream; that close fails here.
    # This only models the handler's side: the flusher survives, nothing points at the
    # closed stream, and the next record starts a new one. FlakyStream's failed close
    # really finishes the xz stream first, which is why everything stays readable below.
    # A real failed finish (say ENOSPC on the last write) can leave part of the stream on
    # disk, and the stream started after it then makes the rest of that file undecodable;
    # nothing here recovers or tests for that.
    tl = make_tee_logger(tmp_path, in_place_compression="xz", flush_interval=0.05)
    handler = _compressed_handler(tl)
    errors = _record_errors(handler)
    tl.info("before")
    for failures in (1, 1, 0):
        handler.acquire()
        try:
            if handler.stream is None:
                handler.stream = handler._open()
            stream = FlakyStream(handler.stream, failures)
        finally:
            handler.release()
        _install(handler, stream)
        assert stream.attempted.wait(10), "the flusher stopped after a failed close"
        handler.acquire()
        try:
            # never left pointing at the closed stream; the next record opens a new one
            assert handler.stream is None
            assert not handler._dirty
        finally:
            handler.release()
    tl.warning("after")
    path = Path(tl.logFileName)
    close_tee_logger(tl)
    assert len(errors) == 1, errors
    # readable only because the fake's close finished the stream (see above)
    text = read_log_text(path)
    assert "before" in text and "after" in text


def test_flush_retries_a_stream_that_failed_to_flush(tmp_path):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=3600)
    handler = _compressed_handler(tl)
    tl.info("buffered")
    handler.acquire()
    try:
        stream = handler.stream = FlakyStream(handler.stream, failures=1)
    finally:
        handler.release()
    with pytest.raises(OSError):
        handler.flush()
    # still dirty, on the same stream, so the next flush retries it
    assert handler._dirty and handler.stream is stream
    handler.flush()
    assert not handler._dirty and stream.attempts == 2
    path = Path(tl.logFileName)
    close_tee_logger(tl)
    assert "buffered" in read_log_text(path)


def _log_in_thread(tl, msg, log=None, timeout=10):
    """Log ``msg`` (with ``log()``, default ``tl.info``) from a worker thread; return False if it is still stuck after ``timeout``."""
    errors = []

    def run():
        try:
            (log or (lambda: tl.info(msg)))()
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    worker = threading.Thread(target=run, name="rollover-test")
    worker.daemon = True  # a deadlocked worker must not keep the test run alive
    worker.start()
    worker.join(timeout)
    assert not errors, errors
    return not worker.is_alive()


def _past_midnight(monkeypatch, tl, during_open):
    """Move the clock two days on and run ``during_open()`` inside _rollover, before the switch."""
    real_open = tl._open_log_file
    opens = []

    def open_log_file():
        opens.append(tl.logFileName)
        during_open()
        return real_open()

    monkeypatch.setattr(tl, "_open_log_file", open_log_file)
    later = time.time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    return opens


def test_logging_from_inside_rollover_does_not_deadlock(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path)
    tl.info("before-midnight")
    first = Path(tl.logFileName)
    # a signal handler that logs while this thread is in the middle of the rollover
    opens = _past_midnight(monkeypatch, tl, lambda: tl.info("from-signal-handler"))
    finished = _log_in_thread(tl, "after-midnight")
    monkeypatch.undo()
    second = Path(tl.logFileName)
    close_tee_logger(tl)
    assert finished, "logging from inside _rollover deadlocked"
    # one rollover, not a second one nested inside the first
    assert len(opens) == 1
    assert first != second
    old_text, new_text = read_log_text(first), read_log_text(second)
    # the re-entrant record went to the handler that was current at the time
    assert "from-signal-handler" in old_text and "from-signal-handler" not in new_text
    assert "after-midnight" in new_text and "Continued from" in new_text


def test_other_threads_wait_for_a_rollover_in_progress(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path)
    others = []

    def start_other_thread():
        other = threading.Thread(target=tl.info, args=("other-thread",))
        other.daemon = True
        other.start()
        other.join(0.2)
        others.append((other, other.is_alive()))

    opens = _past_midnight(monkeypatch, tl, start_other_thread)
    finished = _log_in_thread(tl, "after-midnight")
    (other, waited), = others
    other.join(10)
    other_finished = not other.is_alive()
    monkeypatch.undo()
    second = Path(tl.logFileName)
    close_tee_logger(tl)
    assert finished and other_finished
    assert waited, "another thread logged past a rollover in progress"
    assert len(opens) == 1, "another thread rolled over a second time"
    assert "other-thread" in read_log_text(second)


def test_print_table_reaches_the_console_in_one_write(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path, suppressPrintout=False)
    out = InterruptingStream(lambda: out.write("other thread\n"))
    monkeypatch.setattr(sys, "stdout", out)
    tl.printTable([["name", "value"], ["x", "1"]])
    close_tee_logger(tl)
    assert out.writes[0].startswith("[INFO] name | value\n") and out.writes[0].endswith("\n\n")
    assert out.writes[1:] == ["other thread\n"]


def test_stderr_fallback_writes_each_record_once(tmp_path, monkeypatch, capsys):
    # Python 3.6 and 3.7's StreamHandler write the message and the newline separately
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    monkeypatch.setattr(Tee_Logger.tempfile, "gettempdir", lambda: str(blocker / "tmp"))
    tl = make_tee_logger(tmp_path, systemLogFileDir=str(blocker / "logs"))
    (handler,) = tl.logger.handlers
    assert not isinstance(handler, Tee_Logger.logging.FileHandler)
    err = InterruptingStream(lambda: err.write("other thread\n"))
    handler.stream = err
    tl.info("to stderr")
    close_tee_logger(tl)
    assert err.writes[0].endswith("to stderr\n")
    assert err.writes[1:] == ["other thread\n"]


def test_rollover_survives_the_old_log_failing_to_close(tmp_path, monkeypatch, capsys):
    tl = make_tee_logger(tmp_path, in_place_compression="xz", flush_interval=3600)
    tl.info("before-midnight")
    handler = _compressed_handler(tl)
    handler.acquire()
    try:
        handler.stream = FlakyStream(handler.stream, failures=1)
        handler._dirty = True
    finally:
        handler.release()
    later = time.time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    # the old log's final flush fails (say, a full disk) while closing it at midnight
    tl.info("after-midnight")
    monkeypatch.undo()
    second = Path(tl.logFileName)
    handlers = list(tl.logger.handlers)
    close_tee_logger(tl)
    # the switch still happened, rather than leaving the logger with no handler at all
    assert handlers and all(h.baseFilename == str(second) for h in handlers)
    assert "after-midnight" in read_log_text(second)
    assert "No space left on device" in capsys.readouterr().err


@pytest.fixture
def flusher_watch(monkeypatch):
    """Count the flusher threads started, and capture stderr (where a dying thread's traceback goes)."""
    import io

    class Watch:
        started = 0
        err = io.StringIO()

        @property
        def tracebacks(self):
            return self.err.getvalue().count("Exception in thread teeLogger-flush")

    watch = Watch()
    real_start = threading.Thread.start

    def counting_start(thread):
        if thread.name == "teeLogger-flush":
            watch.started += 1
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", counting_start)
    monkeypatch.setattr(sys, "stderr", watch.err)
    return watch


def test_infinite_flush_interval_starts_no_flusher(tmp_path, flusher_watch):
    # "flush only on WARNING+": a timer that never fires needs no thread
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=float("inf"))
    for i in range(100):
        tl.info(f"rec-{i}")
    handler = _compressed_handler(tl)
    time.sleep(0.1)
    handler.acquire()
    try:
        assert handler._dirty  # INFO records are only buffered
    finally:
        handler.release()
    tl.warning("flush-now")
    handler.acquire()
    try:
        assert not handler._dirty  # WARNING+ still flushes
    finally:
        handler.release()
    path = Path(tl.logFileName)
    assert "flush-now" in salvage_log_text(path)  # on disk before close
    tl.info("last")
    close_tee_logger(tl)
    assert "last" in read_log_text(path)  # close flushes
    assert flusher_watch.started == 0
    assert flusher_watch.tracebacks == 0, flusher_watch.err.getvalue()


@pytest.mark.parametrize("interval", [1e12, 2 * threading.TIMEOUT_MAX])
def test_huge_flush_interval_runs_one_working_flusher(tmp_path, flusher_watch, interval):
    # the interval itself is finite, but longer than Event.wait() accepts
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=interval)
    for i in range(100):
        tl.info(f"rec-{i}")
        time.sleep(0.001)
    time.sleep(0.2)
    handler = _compressed_handler(tl)
    assert handler._flusher_stop is not None
    tl.warning("flush-now")
    path = Path(tl.logFileName)
    assert "flush-now" in salvage_log_text(path)
    close_tee_logger(tl)
    assert flusher_watch.started == 1
    assert flusher_watch.tracebacks == 0, flusher_watch.err.getvalue()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the dying flushers
def test_a_flusher_that_keeps_dying_is_not_restarted_per_record(tmp_path, monkeypatch, flusher_watch):
    # make every wait fail at once, as an out-of-range timeout did
    monkeypatch.setattr(Tee_Logger, "_FLUSH_WAIT_MAX", float("inf"), raising=False)
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=1e12)
    for i in range(100):
        tl.info(f"rec-{i}")
        time.sleep(0.002)
    time.sleep(0.2)
    close_tee_logger(tl)
    assert flusher_watch.started <= 2, flusher_watch.started
    assert flusher_watch.tracebacks <= 2, flusher_watch.err.getvalue()


def _flusher_threads():
    return [t for t in threading.enumerate() if t.name == "teeLogger-flush"]


def _wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_records_logged_after_a_failed_tick_are_still_flushed_by_the_timer(tmp_path, flusher_watch):
    # one unexpected failure inside the flusher, then records, then silence: nothing but the
    # timer can write them (no later record, no close), so the flusher must have carried on
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=0.2)
    handler = _compressed_handler(tl)
    errors = _record_errors(handler)
    real_flush = handler._background_flush
    failed = []

    def failing_once():
        if not failed:
            failed.append(True)
            raise ValueError("one-time failure inside the flusher")
        return real_flush()

    handler._background_flush = failing_once
    tl.info("trigger")  # makes the handler dirty, so the next tick has something to flush
    assert _wait_for(lambda: failed), "the injected failure never happened"
    for i in range(8):
        tl.info(f"burst-{i}")
    path = Path(tl.logFileName)
    assert _wait_for(lambda: "burst-7" in salvage_log_text(path), timeout=5), "the timer did not flush the records"
    close_tee_logger(tl)
    assert len(errors) == 1 and isinstance(errors[0][1], ValueError), errors
    assert flusher_watch.started == 1  # the same thread carried on; none was started to replace it


def _log_until_dirty(tl, handler, prefix, count=100):
    """Log ``count`` records, then more until something is buffered (a record that finds the interval over flushes itself)."""
    for i in range(count):
        tl.info(f"{prefix}-{i}")
    for _ in range(10):
        handler.acquire()
        try:
            if handler._dirty:
                return
        finally:
            handler.release()
        tl.info(f"{prefix}-more")
    raise AssertionError("the handler never held a buffered record")


def test_a_tick_that_always_fails_costs_one_thread_and_one_report(tmp_path, flusher_watch):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=0.1)
    handler = _compressed_handler(tl)
    errors = _record_errors(handler)
    ticks = []

    def failing():
        ticks.append(time.monotonic())
        raise ValueError("every tick fails")

    handler._background_flush = failing
    _log_until_dirty(tl, handler, "rec")
    # the failure really happens, repeatedly, so the loop is alive and trying again
    assert _wait_for(lambda: len(ticks) >= 3), f"only {len(ticks)} tick(s)"
    (thread,) = _flusher_threads()
    assert thread.is_alive()
    _log_until_dirty(tl, handler, "more")
    seen = len(ticks)
    assert _wait_for(lambda: len(ticks) > seen)
    assert _flusher_threads() == [thread]
    close_tee_logger(tl)
    assert flusher_watch.started == 1, flusher_watch.started
    assert flusher_watch.tracebacks == 0
    assert len(errors) == 1, errors  # reported once, not once per tick or per record


def test_a_flusher_thread_that_cannot_be_started_is_retried_after_a_pause(tmp_path, monkeypatch):
    real_start = threading.Thread.start
    attempts = []
    allow = []

    def start(thread):
        if thread.name != "teeLogger-flush":
            return real_start(thread)
        attempts.append(time.monotonic())
        if not allow:
            raise RuntimeError("can't start new thread")
        return real_start(thread)

    errors = []
    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(Tee_Logger._CompressedFileHandler, "handleError",
                        lambda handler, record: errors.append(sys.exc_info()[1]))
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=0.05)
    handler = _compressed_handler(tl)
    for i in range(100):
        tl.info(f"rec-{i}")
    # not a thread attempt, nor a report, per record
    assert len(attempts) == 1 and len(errors) == 1, (attempts, errors)
    assert handler._flusher_stop is None
    time.sleep(1.1)  # the pause is one second at least
    allow.append(True)
    tl.info("flushes, as an interval has passed")
    tl.info("after the pause")  # buffered, so it needs a flusher
    assert len(attempts) == 2 and handler._flusher_stop is not None
    path = Path(tl.logFileName)
    assert _wait_for(lambda: "after the pause" in salvage_log_text(path), timeout=5)
    close_tee_logger(tl)


@pytest.mark.parametrize("interval", [float("nan"), "30", None])
def test_flush_interval_must_be_a_number_and_not_nan(tmp_path, interval):
    with pytest.raises((ValueError, TypeError)):
        make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=interval)
    with pytest.raises((ValueError, TypeError)):
        Tee_Logger._CompressedFileHandler(str(tmp_path / "x.log"), flush_interval=interval, delay=True)


@pytest.mark.parametrize("interval", [decimal.Decimal("0.2"), fractions.Fraction(1, 5)])
def test_a_non_float_flush_interval_works_as_a_float(tmp_path, flusher_watch, interval):
    # Decimal and Fraction are real numbers that pass the NaN check but do not mix with
    # float arithmetic; the flusher died on its first tick and then again on every record
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=interval)
    handler = _compressed_handler(tl)
    assert type(tl.flush_interval) is float and type(handler.flush_interval) is float
    for i in range(100):
        tl.info(f"rec-{i}")
        time.sleep(0.002)
    path = Path(tl.logFileName)
    deadline = time.monotonic() + 10
    while "rec-99" not in salvage_log_text(path) and time.monotonic() < deadline:
        time.sleep(0.05)
    close_tee_logger(tl)
    # the timer flushed the records before close, from one flusher that never died
    assert "rec-99" in salvage_log_text(path)
    assert flusher_watch.started == 1, flusher_watch.started
    assert flusher_watch.tracebacks == 0, flusher_watch.err.getvalue()


def test_negative_flush_interval_flushes_every_record(tmp_path):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=-1)
    tl.info("each")
    handler = _compressed_handler(tl)
    assert not handler._dirty
    path = Path(tl.logFileName)
    assert "each" in salvage_log_text(path)
    close_tee_logger(tl)


class DeadTerminal:
    """A stderr that cannot be written, like a daemon's whose terminal went away."""

    def write(self, data):
        raise OSError(errno.EIO, "Input/output error")

    def flush(self):
        pass

    def isatty(self):
        return False


def test_diagnose_never_raises(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path)
    monkeypatch.setattr(sys, "stderr", DeadTerminal())
    tl._diagnose("nowhere to say it")
    tl._diagnose(OSError("not even a string"), "warning")
    close_tee_logger(tl)


def test_rollover_attaches_the_new_log_when_stderr_is_dead_too(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path, in_place_compression="xz", flush_interval=3600)
    tl.info("before-midnight")
    handler = _compressed_handler(tl)
    handler.acquire()
    try:
        handler.stream = FlakyStream(handler.stream, failures=1)
        handler._dirty = True
    finally:
        handler.release()
    later = time.time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    # the old log fails to close, and the report of that has nowhere to go either
    monkeypatch.setattr(sys, "stderr", DeadTerminal())
    tl.info("after-midnight")  # must not raise
    monkeypatch.undo()
    second = Path(tl.logFileName)
    handlers = list(tl.logger.handlers)
    close_tee_logger(tl)
    assert handlers and all(h.baseFilename == str(second) for h in handlers)
    assert "after-midnight" in read_log_text(second)


def test_records_from_other_threads_are_kept_while_the_old_log_closes(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path)
    tl.info("before-midnight")
    first = Path(tl.logFileName)
    old = tl.logger.handlers[0]
    real_close = old.close
    finished = {}

    def slow_close():
        old.close = real_close  # once only: logging.shutdown closes it again at exit
        # stands in for a slow close (finishing a big xz stream): the rollover thread is
        # busy here while other threads log, an INFO that nothing else would keep and an
        # ERROR that would otherwise only reach logging.lastResort
        for msg, log in (("other-thread-INFO", tl.info), ("other-thread-ERROR", tl.error)):
            finished[msg] = _log_in_thread(tl, msg, lambda: log(msg))
        real_close()

    old.close = slow_close
    later = time.time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    tl.info("after-midnight")
    monkeypatch.undo()
    second = Path(tl.logFileName)
    close_tee_logger(tl)
    assert first != second
    assert finished == {"other-thread-INFO": True, "other-thread-ERROR": True}
    texts = read_log_text(first), read_log_text(second)
    for msg in ("other-thread-INFO", "other-thread-ERROR", "after-midnight"):
        # in a log file, and in one of them only
        assert sum(text.count(msg) for text in texts) == 1, (msg, texts)
    assert texts[1].count("Continued from") == 1


def test_the_logger_always_has_a_handler_during_a_rollover(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path)
    tl.info("before-midnight")
    old = tl.logger.handlers[0]
    real_close = old.close
    seen = []

    def close():
        old.close = real_close  # once only: logging.shutdown closes it again at exit
        seen.append(list(tl.logger.handlers))
        real_close()

    old.close = close
    later = time.time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    tl.info("after-midnight")
    monkeypatch.undo()
    new = list(tl.logger.handlers)
    close_tee_logger(tl)
    assert len(new) == 1 and new[0] is not old
    # while the old handler was closing, the new one was already the logger's only handler
    assert seen == [new]
