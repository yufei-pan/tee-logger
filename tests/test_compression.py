import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text, salvage_log_text

BACKENDS = [None, "gzip", "bz2", "xz", "zstd"]
TIME_IN_NAME = re.compile(r"_\d{2}-\d{2}-\d{2}(?:\.|$)")


def _suffix_for(backend):
    return {None: ".log", "gzip": ".gz", "bz2": ".bz2", "xz": ".xz", "zstd": ".zst"}[backend]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("binary_mode", [True, False])
@pytest.mark.parametrize("collapse", [True, False, "default"])
def test_compression_roundtrip(tmp_path, backend, binary_mode, collapse):
    if backend == "zstd":
        pytest.importorskip("compression.zstd")
    kwargs = {
        "programName": f"cmp_{backend}_{binary_mode}_{collapse}".replace(".", "p"),
        "binary_mode": binary_mode,
        "in_place_compression": backend,
    }
    if collapse != "default":
        kwargs["collapse_single_day_logs"] = collapse
    tl = make_tee_logger(tmp_path, **kwargs)
    msg = f"payload-{backend}-{binary_mode}-{collapse}"
    tl.info(msg)
    from pathlib import Path

    path = Path(tl.logFileName)
    collapsed = tl.collapse_single_day_logs
    close_tee_logger(tl)

    assert path.exists()
    assert path.name.endswith(_suffix_for(backend))
    content = read_log_text(path)
    assert msg in content

    if collapsed:
        assert TIME_IN_NAME.search(path.name) is None
    else:
        assert TIME_IN_NAME.search(path.name) is not None


@pytest.mark.parametrize("backend, level", [("gzip", 10), ("bz2", 0), ("xz", 99), ("zstd", 99), ("gzip", "high")])
def test_a_level_the_backend_refuses_fails_when_the_logger_is_made(tmp_path, monkeypatch, capsys, backend, level):
    # rather than at every flush, which would drop every record; records go to stderr instead
    _skip_if_unavailable(backend)
    import Tee_Logger

    monkeypatch.setattr(Tee_Logger.tempfile, "gettempdir", lambda: str(tmp_path / "shared"))
    tl = make_tee_logger(tmp_path, in_place_compression=backend, compression_level=level)
    tl.warning("still seen")
    close_tee_logger(tl)
    err = capsys.readouterr().err
    assert "Failed to create log file" in err and "still seen" in err


def test_compression_level_accepted(tmp_path):
    tl = make_tee_logger(
        tmp_path,
        programName="cmp_level",
        in_place_compression="gzip",
        compression_level=3,
    )
    tl.info("leveled")
    close_tee_logger(tl)
    content = read_log_text(find_log_files(tmp_path / "cmp_level_log")[-1])
    assert "leveled" in content


@pytest.mark.zstd
def test_zstd_marker_path(tmp_path):
    pytest.importorskip("compression.zstd")
    tl = make_tee_logger(tmp_path, programName="cmp_zstd_mark", in_place_compression="zstd")
    tl.info("zstd-msg")
    close_tee_logger(tl)
    files = find_log_files(tmp_path / "cmp_zstd_mark_log")
    assert files[-1].name.endswith(".zst")
    assert "zstd-msg" in read_log_text(files[-1])


SRC = Path(__file__).resolve().parents[1] / "src"
CRASH_BACKENDS = ["gzip", "bz2", "xz", "zstd"]


def _child(tmp_path, backend, body, crash=True, **logger_kwargs):
    """Start a child that logs with ``body``, then dies via os._exit (no handler close, no atexit)
    or, without ``crash``, exits normally."""
    script = tmp_path / "child.py"
    script.write_text(
        "import os, sys, time\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "from Tee_Logger import teeLogger\n"
        f"tag = sys.argv[1]\n"
        f"tl = teeLogger(systemLogFileDir={str(tmp_path)!r}, programName='crash', "
        f"in_place_compression={backend!r}, suppressPrintout=True, "
        f"compressLogAfterMonths=0, deleteLogAfterYears=0, **{logger_kwargs!r})\n"
        + body
        + ("os._exit(17)\n" if crash else ""),
        encoding="utf-8",
    )
    return lambda tag="A": subprocess.Popen([sys.executable, str(script), tag], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _finish(proc, crash=True):
    _out, err = proc.communicate(timeout=60)
    assert proc.returncode == (17 if crash else 0), err.decode()


def _the_log(tmp_path):
    files = find_log_files(tmp_path / "crash_log")
    assert len(files) == 1, files
    return files[0]


def _run_and_crash(tmp_path, backend, body, **logger_kwargs):
    _finish(_child(tmp_path, backend, body, **logger_kwargs)())
    return salvage_log_text(_the_log(tmp_path))


def _skip_if_unavailable(backend):
    if backend == "zstd":
        pytest.importorskip("compression.zstd")


@pytest.mark.parametrize("backend", CRASH_BACKENDS)
def test_warning_flushes_everything_before_a_crash(tmp_path, backend):
    _skip_if_unavailable(backend)
    text = _run_and_crash(
        tmp_path,
        backend,
        "for i in range(300):\n    tl.info('info line %d' % i)\ntl.log('warning line', 'warning')\n",
    )
    assert "info line 0\n" in text
    assert "info line 299\n" in text
    assert "warning line" in text


@pytest.mark.parametrize("backend", CRASH_BACKENDS)
def test_idle_tail_is_flushed_by_interval(tmp_path, backend):
    _skip_if_unavailable(backend)
    text = _run_and_crash(
        tmp_path,
        backend,
        "for i in range(50):\n    tl.info('quiet line %d' % i)\ntime.sleep(1.5)\n",
        flush_interval=0.3,
    )
    assert "quiet line 0\n" in text
    assert "quiet line 49\n" in text


@pytest.mark.parametrize("backend", CRASH_BACKENDS)
def test_a_restart_after_a_crash_keeps_the_log_readable(tmp_path, backend):
    # the second run appends to the day's file the killed one left behind
    _skip_if_unavailable(backend)
    body = ("for i in range(2000):\n    tl.info('%s line %d' % (tag, i))\n"
            "tl.log('%s warning' % tag, 'warning')\n"
            "for i in range(2000):\n    tl.info('%s unwritten %d' % (tag, i))\n")
    _finish(_child(tmp_path, backend, body)("first"))
    _finish(_child(tmp_path, backend, body, crash=False)("second"), crash=False)
    text = read_log_text(_the_log(tmp_path))  # the standard reader, which stops at a broken member
    assert text.count("first line") == 2000 and "first unwritten" not in text
    assert text.count("second line") == 2000 and text.count("second unwritten") == 2000


@pytest.mark.parametrize("backend", CRASH_BACKENDS)
def test_two_processes_writing_one_log_keep_it_readable(tmp_path, backend):
    _skip_if_unavailable(backend)
    body = ("for i in range(3000):\n    tl.info('%s line %d %s' % (tag, i, 'x' * (i % 50)))\n"
            "    if i % 97 == 0:\n        tl.log('%s warning %d' % (tag, i), 'warning')\n")
    start = _child(tmp_path, backend, body, crash=False, flush_interval=0.05)
    procs = [start("A"), start("B")]
    for proc in procs:
        _finish(proc, crash=False)
    text = read_log_text(_the_log(tmp_path))
    assert text.count("A line") == 3000 and text.count("B line") == 3000


def _varied_lines(n, seed=7):
    rng = random.Random(seed)
    words = ["disk", "sda", "scrub", "ok", "retry", "host", "node%02d", "latency", "ms", "write", "read", "error=0"]
    return [
        " ".join((w % rng.randint(1, 64)) if "%" in w else w for w in rng.sample(words, 6))
        + " value=%d" % rng.randint(0, 100000)
        for _ in range(n)
    ]


def _log_and_measure(tmp_path, name, lines, **kwargs):
    tl = make_tee_logger(tmp_path, programName=name, **kwargs)
    for line in lines:
        tl.info(line)
    path = Path(tl.logFileName)
    close_tee_logger(tl)
    return path.stat().st_size, path


def test_batched_flush_compresses_better_than_per_record(tmp_path):
    lines = _varied_lines(3000)
    per_record, _ = _log_and_measure(tmp_path, "flush_each", lines, in_place_compression="gzip", flush_interval=0)
    batched, path = _log_and_measure(tmp_path, "flush_batch", lines, in_place_compression="gzip", flush_interval=30)
    assert batched < 0.75 * per_record
    assert lines[-1] in read_log_text(path)


def _random_block(size, seed=11):
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return ["".join(rng.choice(alphabet) for _ in range(100)) for _ in range(size // 100)]


@pytest.mark.parametrize(
    "backend,low,high,block",
    [
        # gzip: level 0 stores, level 9 deflates
        ("gzip", 0, 9, 50_000),
        # bz2: a block repeated 300 KB later only fits in level 9's 900 KB BWT block
        ("bz2", 1, 9, 300_000),
        # xz: preset 0 has a 256 KiB dictionary, preset 6 has 8 MiB
        ("xz", 0, 6, 300_000),
        # zstd: level 19 searches far harder than level 1
        ("zstd", 1, 19, 300_000),
    ],
)
def test_compression_level_takes_effect(tmp_path, backend, low, high, block):
    _skip_if_unavailable(backend)
    lines = _random_block(block)
    lines = lines + lines  # identical content, `block` bytes apart, in one batch (_FLUSH_BATCH_MAX)
    low_size, _ = _log_and_measure(tmp_path, f"lvl_{backend}_lo", lines, in_place_compression=backend, compression_level=low)
    high_size, _ = _log_and_measure(tmp_path, f"lvl_{backend}_hi", lines, in_place_compression=backend, compression_level=high)
    assert high_size < 0.75 * low_size
