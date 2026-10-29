"""Regression tests for output from several threads or a signal handler at once."""
import decimal
import errno
import fractions
import gc
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

import Tee_Logger
from Tee_Logger import printWithColor
from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text, salvage_log_text


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
        if not data:  # print(..., end='') writes its empty end separately
            return 0
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


def test_print_with_color_goes_through_a_replaced_print(monkeypatch):
    # a program that replaces print() (a TUI, a progress bar) sees the console lines
    import builtins

    seen = []
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: seen.append((args, kwargs)))
    printWithColor("routed", "info", disable_colors=True)
    assert seen == [(("[INFO] routed\n",), {"end": "", "file": sys.stdout})]


def test_print_with_color_without_stdout(monkeypatch):
    # print() is a no-op when sys.stdout is None (pythonw); so is printWithColor
    monkeypatch.setattr(sys, "stdout", None)
    printWithColor("nowhere", "info", disable_colors=True)


class FullDisk:
    """Wraps a compressed log's file; its first ``failures`` writes fail as on a full disk.

    With ``short`` such a write puts half the data on disk first and returns that
    count, as write() does when the disk fills up partway through.
    """

    def __init__(self, real, failures=1, short=False):
        self.real = real
        self.failures = failures
        self.short = short
        self.attempts = 0
        self.attempted = threading.Event()

    def write(self, data):
        self.attempts += 1
        try:
            if self.failures > 0:
                self.failures -= 1
                if self.short:
                    return self.real.write(data[: len(data) // 2])
                raise OSError(errno.ENOSPC, "No space left on device")
            return self.real.write(data)
        finally:
            self.attempted.set()

    def __getattr__(self, name):
        return getattr(self.real, name)


def _compressed_handler(tl):
    (handler,) = [h for h in tl.logger.handlers if isinstance(h, Tee_Logger._CompressedFileHandler)]
    return handler


def _fill_disk(handler, **kwargs):
    handler.acquire()
    try:
        handler.stream = FullDisk(handler.stream, **kwargs)
    finally:
        handler.release()
    return handler.stream


def _record_errors(handler):
    errors = []
    handler.handleError = lambda record: errors.append((record, sys.exc_info()[1]))
    return errors


def _wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


@pytest.mark.parametrize("backend", ["gzip", "xz"])
def test_a_short_write_leaves_no_partial_member(tmp_path, backend):
    # half a member would hide everything appended after it from the decompressors
    tl = make_tee_logger(tmp_path, in_place_compression=backend, flush_interval=3600)
    handler = _compressed_handler(tl)
    errors = _record_errors(handler)
    tl.warning("before the disk filled up")
    disk = _fill_disk(handler, short=True)
    tl.warning("lost to the full disk")
    handler.stream = disk.real
    tl.warning("after space was freed")
    path = Path(tl.logFileName)
    close_tee_logger(tl)
    assert len(errors) == 1 and "wrote" in str(errors[0][1]), errors
    text = read_log_text(path)  # the whole file decodes
    assert "before the disk filled up" in text and "after space was freed" in text
    assert "lost to the full disk" not in text


def test_a_failed_background_write_is_reported_and_the_flusher_carries_on(tmp_path, flusher_watch):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=0.05)
    handler = _compressed_handler(tl)
    errors = _record_errors(handler)
    disk = _fill_disk(handler)
    tl.info("dropped with the failed write")
    assert disk.attempted.wait(10), "the flusher never tried to write"
    tl.info("written by the timer later")
    path = Path(tl.logFileName)
    assert _wait_for(lambda: "written by the timer later" in salvage_log_text(path))
    close_tee_logger(tl)
    assert len(errors) == 1 and errors[0][1].errno == errno.ENOSPC, errors
    assert "dropped with the failed write" not in read_log_text(path)
    assert flusher_watch.started == 1


def test_the_flusher_outlives_a_failed_write_it_cannot_report(tmp_path, monkeypatch):
    # stderr closed by the program: logging's own report of the failure raises ValueError
    import io

    closed = io.StringIO()
    closed.close()
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=0.05)
    handler = _compressed_handler(tl)
    disk = _fill_disk(handler)
    monkeypatch.setattr(sys, "stderr", closed)
    tl.info("dropped with the failed write")
    assert disk.attempted.wait(10), "the flusher never tried to write"
    tl.info("written by the timer later")
    path = Path(tl.logFileName)
    assert _wait_for(lambda: "written by the timer later" in salvage_log_text(path)), "the flusher died"
    monkeypatch.undo()
    close_tee_logger(tl)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # fork() with the flusher thread running
def test_records_buffered_before_a_fork_are_written_once(tmp_path):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=60)
    handler = _compressed_handler(tl)
    tl.info("buffered in the parent")
    path = Path(tl.logFileName)
    pid = os.fork()
    if pid == 0:  # the child: never return into pytest
        code = 1
        try:
            # written before the fork, as a daemon's parent leaves with os._exit (3.7+ only:
            # 3.6 runs no at-fork hooks, a known limitation)
            if hasattr(os, "register_at_fork") and "buffered in the parent" not in read_log_text(path):
                os._exit(4)
            tl.info("from the child")
            code = 0 if handler._flusher_stop is not None else 3  # a flusher of its own
            tl.warning("child warning")
        finally:
            os._exit(code)
    _pid, status = os.waitpid(pid, 0)
    tl.warning("parent warning")
    close_tee_logger(tl)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status
    text = read_log_text(path)
    assert text.count("buffered in the parent") == 1, text
    assert all(msg in text for msg in ("from the child", "child warning", "parent warning"))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # fork() with the flusher thread running
def test_a_multiprocessing_worker_writes_its_last_records(tmp_path):
    # the worker leaves with os._exit, which skips logging's own flush at exit
    import multiprocessing

    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=60)
    worker = multiprocessing.get_context("fork").Process(target=tl.info, args=("from the worker",))
    worker.start()
    worker.join(30)
    path = Path(tl.logFileName)
    close_tee_logger(tl)
    assert worker.exitcode == 0
    assert "from the worker" in read_log_text(path)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.parametrize("method", ["fork", "forkserver"])
def test_a_worker_that_imports_tee_logger_once_it_runs_writes_its_last_records(tmp_path, method):
    # in a fresh interpreter, as this one imported Tee_Logger long ago
    import multiprocessing
    import subprocess

    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"no {method} here")
    tests = Path(__file__).resolve().parent
    script = tmp_path / "lazy_parent.py"
    script.write_text(
        "import logging, multiprocessing, sys\n"  # logging: as nearly every program has
        f"sys.path[:0] = [{str(tests.parent / 'src')!r}, {str(tests)!r}]\n"
        "import lazy_worker\n"
        "if __name__ == '__main__':\n"
        f"    worker = multiprocessing.get_context({method!r}).Process(\n"
        f"        target=lazy_worker.log_once, args=({str(tmp_path)!r}, 'imported once it ran'))\n"
        "    worker.start()\n"
        "    worker.join(60)\n"
        "    sys.exit(0 if worker.exitcode == 0 else 1)\n",  # None: still running
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode()
    files = find_log_files(tmp_path / "lazy_worker_log")
    assert files and "imported once it ran" in read_log_text(files[-1])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_a_forkserver_worker_writes_what_it_logs_while_it_starts(tmp_path):
    # a module the worker imports to unpickle its target logs, and the target then logs nothing
    import multiprocessing
    import subprocess

    if "forkserver" not in multiprocessing.get_all_start_methods():
        pytest.skip("no forkserver here")
    tests = Path(__file__).resolve().parent
    script = tmp_path / "startup_parent.py"
    script.write_text(
        # logging: then 3.13+ forkserver workers have no logging.shutdown at exit either
        "import logging, multiprocessing, os, sys\n"
        f"sys.path[:0] = [{str(tests.parent / 'src')!r}, {str(tests)!r}]\n"
        "if __name__ == '__main__':\n"
        "    import startup_worker  # here, not in the forkserver; it logs nothing in this process\n"
        f"    os.environ['TL_STARTUP_ROOT'] = {str(tmp_path)!r}\n"
        "    worker = multiprocessing.get_context('forkserver').Process(target=startup_worker.nothing)\n"
        "    worker.start()\n"
        "    worker.join(60)\n"
        "    print(worker.pid)\n"
        "    sys.exit(0 if worker.exitcode == 0 else 1)\n",  # None: still running
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode()
    worker_pid = int(proc.stdout.split()[-1])
    files = find_log_files(tmp_path / "startup_worker_log")
    assert files and f"imported in {worker_pid}\n" in read_log_text(files[-1])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_a_child_closing_its_logger_does_not_wait_for_the_parents_flusher(tmp_path):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=60)
    tl.info("starts the flusher")
    handler = _compressed_handler(tl)
    stop = handler._flusher_stop
    stop._cond.acquire()  # as when the fork lands while the parent's flusher is in stop.wait()
    try:
        pid = os.fork()
        if pid == 0:  # the child: never return into pytest
            threading.Timer(5, os._exit, args=(9,)).start()
            code = 1
            try:
                handler.close()
                code = 0
            finally:
                os._exit(code)
    finally:
        stop._cond.release()
    _pid, status = os.waitpid(pid, 0)
    close_tee_logger(tl)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "the child hung in close()"


def test_tee_logger_imports_while_another_thread_imports_multiprocessing_util():
    # in a fresh interpreter, with the module as sys.modules holds it before its body runs
    import subprocess

    src = Path(__file__).resolve().parents[1] / "src"
    code = ("import sys, types\n"
            "sys.modules['multiprocessing.util'] = types.ModuleType('multiprocessing.util')\n"
            f"sys.path.insert(0, {str(src)!r})\n"
            "import Tee_Logger\n")
    proc = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode()


def test_a_record_logged_while_another_thread_imports_multiprocessing_util_is_written(tmp_path, monkeypatch, capsys):
    # sys.modules holds a module from the start of its import; its names come as its body runs
    import types

    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=60)
    monkeypatch.setitem(sys.modules, "multiprocessing.util", types.ModuleType("multiprocessing.util"))
    monkeypatch.setattr(Tee_Logger, "_WORKER_EXIT_FLUSH", None)
    tl.warning("logged mid-import")
    path = Path(tl.logFileName)
    monkeypatch.undo()
    close_tee_logger(tl)
    assert "Logging error" not in capsys.readouterr().err
    assert "logged mid-import" in read_log_text(path)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_a_child_forked_while_another_thread_rolls_over_can_roll_over(tmp_path):
    # the thread holding the rollover lock does not exist in the child
    tl = make_tee_logger(tmp_path)
    held, release = threading.Event(), threading.Event()

    def roll_over_slowly():
        with Tee_Logger._ROLLOVER_LOCK:
            held.set()
            release.wait(10)

    holder = threading.Thread(target=roll_over_slowly, daemon=True)
    holder.start()
    assert held.wait(5)
    pid = os.fork()
    if pid == 0:  # the child: never return into pytest
        threading.Timer(5, os._exit, args=(9,)).start()  # a hang ends the child, reported below
        code = 1
        try:
            later = time.time() + 2 * 86400
            time.time = lambda: later
            tl.info("after midnight in the child")  # from the main thread, not a new one
            code = 0
        finally:
            os._exit(code)
    release.set()
    holder.join(10)
    _pid, status = os.waitpid(pid, 0)
    close_tee_logger(tl)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "the child's rollover hung"


def test_a_fork_no_hook_saw_leaves_the_parents_batch_to_the_parent(tmp_path):
    # Python 3.6 runs no at-fork hooks: the handler tells a fork by its pid
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=60)
    handler = _compressed_handler(tl)
    tl.info("the parent's, still buffered")
    handler._pid = -1  # as in the child of such a fork
    tl.warning("the child's")
    assert handler._pid == os.getpid()
    path = Path(tl.logFileName)
    close_tee_logger(tl)
    text = read_log_text(path)
    assert "the parent's" not in text and "the child's" in text


def test_a_record_logged_while_a_batch_is_compressed_gets_its_own_member(tmp_path):
    # a signal handler that logs a WARNING lands in the middle of a flush on its own thread
    tl = make_tee_logger(tmp_path, in_place_compression="xz", flush_interval=3600)
    handler = _compressed_handler(tl)
    real_compress = handler._compress
    interrupted = []

    def compress(data):
        if not interrupted:
            interrupted.append(True)
            tl.warning("from-signal-handler")
        return real_compress(data)

    handler._compress = compress
    tl.info("buffered")
    tl.warning("flushes")
    path = Path(tl.logFileName)
    close_tee_logger(tl)
    text = read_log_text(path)
    assert interrupted
    assert all(msg in text for msg in ("buffered", "flushes", "from-signal-handler")), text


def test_a_record_reaching_a_closed_handler_is_written_at_once(tmp_path):
    # e.g. from a thread that looked the handler up just before a later teeLogger of the
    # same name closed it: no flusher is left, so it must not wait in memory
    tl = make_tee_logger(tmp_path, in_place_compression="gzip")
    handler = _compressed_handler(tl)
    tl.logger.removeHandler(handler)
    handler.close()
    handler.handle(logging.makeLogRecord({"msg": "late record", "levelno": logging.INFO,
                                          "levelname": "INFO", "callerFileLocation": "x"}))
    path = Path(tl.logFileName)
    assert "late record" in read_log_text(path)
    handler.close()


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


@pytest.mark.parametrize("interval", [float("inf"), float("nan")])
def test_infinite_flush_interval_starts_no_flusher(tmp_path, flusher_watch, interval):
    # "write only on WARNING+": a timer that never fires needs no thread
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=interval)
    for i in range(100):
        tl.info(f"rec-{i}")
    handler = _compressed_handler(tl)
    time.sleep(0.1)
    assert handler._batch  # INFO records are only buffered
    tl.warning("flush-now")
    assert not handler._batch  # WARNING+ still writes
    path = Path(tl.logFileName)
    assert "flush-now" in read_log_text(path)  # on disk before close
    tl.info("last")
    close_tee_logger(tl)
    assert "last" in read_log_text(path)  # close writes
    assert flusher_watch.started == 0
    assert flusher_watch.tracebacks == 0, flusher_watch.err.getvalue()


def test_a_full_batch_is_written_without_waiting_for_the_interval(tmp_path):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=float("inf"))
    for i in range(1100):  # about 1.1 MiB of records
        tl.info(f"rec-{i} " + "x" * 1000)
    path = Path(tl.logFileName)
    assert "rec-0 " in read_log_text(path)  # on disk before close
    close_tee_logger(tl)


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
    assert "flush-now" in read_log_text(path)
    close_tee_logger(tl)
    assert flusher_watch.started == 1
    assert flusher_watch.tracebacks == 0, flusher_watch.err.getvalue()


def test_a_flusher_thread_that_cannot_be_started_is_not_tried_again(tmp_path, monkeypatch):
    # a known limitation (docs/limitations.md): records then wait for a WARNING, a record
    # once the interval is over, or close
    real_start = threading.Thread.start
    attempts = []

    def start(thread):
        if thread.name != "teeLogger-flush":
            return real_start(thread)
        attempts.append(thread)
        raise RuntimeError("can't start new thread")

    errors = []
    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(Tee_Logger._CompressedFileHandler, "handleError",
                        lambda handler, record: errors.append(sys.exc_info()[1]))
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=60)
    for i in range(100):
        tl.info(f"rec-{i}")
    tl.warning("written")
    path = Path(tl.logFileName)
    text = read_log_text(path)
    close_tee_logger(tl)
    # neither a thread attempt nor a report per record, and no record lost
    assert len(attempts) == 1 and len(errors) == 1, (attempts, errors)
    assert "rec-0" in text and "rec-99" in text and "written" in text


@pytest.mark.parametrize("interval, expected", [("30", 30.0), (decimal.Decimal("2"), 2.0), (fractions.Fraction(3, 2), 1.5)])
def test_flush_interval_is_taken_as_a_float(tmp_path, interval, expected):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=interval)
    handler = _compressed_handler(tl)
    assert tl.flush_interval == handler.flush_interval == expected
    assert type(handler.flush_interval) is float
    close_tee_logger(tl)


@pytest.mark.parametrize("interval, error", [(None, TypeError), ("soon", ValueError)])
def test_a_flush_interval_that_is_not_a_number_raises(tmp_path, interval, error):
    with pytest.raises(error):
        make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=interval)


@pytest.mark.parametrize("interval", [None, "30", float("nan")])
@pytest.mark.parametrize("unused", ["uncompressed", "noLog", "devnull"])
def test_flush_interval_is_not_checked_where_it_is_not_used(tmp_path, interval, unused):
    # only a compressed log file has a flush interval
    kwargs = {
        "uncompressed": {},
        "noLog": {"in_place_compression": "gzip", "noLog": True},
        "devnull": {"in_place_compression": "gzip", "systemLogFileDir": "/dev/null"},
    }[unused]
    tl = make_tee_logger(tmp_path, flush_interval=interval, **kwargs)
    tl.info("fine")
    if unused == "uncompressed":
        path = Path(tl.logFileName)
        close_tee_logger(tl)
        assert "fine" in read_log_text(path)
    else:
        close_tee_logger(tl)


def test_negative_flush_interval_flushes_every_record(tmp_path):
    tl = make_tee_logger(tmp_path, in_place_compression="gzip", flush_interval=-1)
    tl.info("each")
    handler = _compressed_handler(tl)
    assert not handler._batch
    path = Path(tl.logFileName)
    assert "each" in read_log_text(path)
    close_tee_logger(tl)


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
    """Move the clock two days on and run ``during_open()`` as the rollover opens the new
    day's file, under the handler lock and before the switch."""
    (handler,) = [h for h in tl.logger.handlers if isinstance(h, logging.FileHandler)]
    real_open = handler._open
    opens = []

    def open_log_file():
        opens.append(handler.baseFilename)
        during_open()
        return real_open()

    monkeypatch.setattr(handler, "_open", open_log_file)
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
    first = Path(tl.logFileName)
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
    # in the new day's file, and in it only
    assert read_log_text(second).count("other-thread") == 1
    assert read_log_text(first).count("other-thread") == 0


def test_print_table_reaches_the_console_in_one_write(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path, suppressPrintout=False)
    out = InterruptingStream(lambda: out.write("other thread\n"))
    monkeypatch.setattr(sys, "stdout", out)
    tl.printTable([["name", "value"], ["x", "1"]])
    close_tee_logger(tl)
    assert out.writes[0].startswith("[INFO] name | value\n") and out.writes[0].endswith("\n\n")
    assert out.writes[1:] == ["other thread\n"]


def test_rollover_survives_the_old_log_failing_to_close(tmp_path, monkeypatch, capsys):
    tl = make_tee_logger(tmp_path, in_place_compression="xz", flush_interval=3600)
    tl.info("before-midnight")
    _fill_disk(_compressed_handler(tl))
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


class DeadTerminal:
    """A stderr that cannot be written, like a daemon's whose terminal went away."""

    def write(self, data):
        raise OSError(errno.EIO, "Input/output error")

    def flush(self):
        pass

    def isatty(self):
        return False


def test_report_prints_nothing_without_a_stderr(monkeypatch, capsys):
    # fd 2 closed at start (or pythonw): not to stdout, which may be the program's data
    monkeypatch.setattr(sys, "stderr", None)
    Tee_Logger._report("nowhere to say it")
    assert capsys.readouterr().out == ""


def test_diagnose_never_raises(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path)
    monkeypatch.setattr(sys, "stderr", DeadTerminal())
    tl._diagnose("nowhere to say it")
    tl._diagnose(OSError("not even a string"), "warning")
    close_tee_logger(tl)


def test_rollover_attaches_the_new_log_when_stderr_is_dead_too(tmp_path, monkeypatch):
    tl = make_tee_logger(tmp_path, in_place_compression="xz", flush_interval=3600)
    tl.info("before-midnight")
    _fill_disk(_compressed_handler(tl))
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


@pytest.mark.parametrize("backend", [None, "gzip"])
def test_a_failed_rollover_keeps_writing_the_old_log_and_retries_an_hour_later(tmp_path, monkeypatch, capsys, backend):
    tl = make_tee_logger(tmp_path, in_place_compression=backend, flush_interval=3600)
    tl.info("before-midnight")
    first = Path(tl.logFileName)
    (handler,) = [h for h in tl.logger.handlers if isinstance(h, logging.FileHandler)]

    def cannot_open():
        raise OSError("simulated: cannot open the new day's file")

    real_open = handler._open
    monkeypatch.setattr(handler, "_open", cannot_open)
    now = time.time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: now)
    tl.info("after-midnight")
    assert "Failed to roll over" in capsys.readouterr().err
    assert Path(tl.logFileName) == first and handler.baseFilename == str(first)
    monkeypatch.setattr(handler, "_open", real_open)
    tl.info("still-before-the-retry")
    assert Path(tl.logFileName) == first
    now += 3601
    tl.info("an-hour-later")
    second = Path(tl.logFileName)
    monkeypatch.undo()
    close_tee_logger(tl)
    assert second != first
    old_text = read_log_text(first)
    assert all(msg in old_text for msg in ("before-midnight", "after-midnight", "still-before-the-retry"))
    assert "an-hour-later" in read_log_text(second) and "an-hour-later" not in old_text


def test_a_rollover_does_not_wait_for_the_logging_module_lock(tmp_path, monkeypatch):
    # A signal handler that logs while its thread holds logging's own lock (in getLogger,
    # say) waits for a rollover another thread is doing; that rollover must not then
    # wait for logging's lock, or neither ever goes on.
    tl = make_tee_logger(tmp_path, in_place_compression="gzip")
    tl.info("before-midnight")
    later = time.time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    # A dead handler that the garbage collector frees takes logging's lock too (its weakref
    # callback), in whatever thread collects it; keep other tests' leftovers out of this one.
    gc.collect()
    gc.disable()
    try:
        with logging._lock:
            finished = _log_in_thread(tl, "after-midnight", timeout=5)
    finally:
        gc.enable()
    monkeypatch.undo()
    second = Path(tl.logFileName)
    close_tee_logger(tl)
    assert finished, "the rollover waited for logging's module lock"
    assert "after-midnight" in read_log_text(second)
