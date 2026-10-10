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


def _run_and_crash(tmp_path, backend, body, **logger_kwargs):
    """Run ``body`` in a child that then dies via os._exit (no handler close, no atexit)."""
    script = tmp_path / "crasher.py"
    script.write_text(
        "import os, sys, time\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "from Tee_Logger import teeLogger\n"
        f"tl = teeLogger(systemLogFileDir={str(tmp_path)!r}, programName='crash', "
        f"in_place_compression={backend!r}, suppressPrintout=True, "
        f"compressLogAfterMonths=0, deleteLogAfterYears=0, **{logger_kwargs!r})\n"
        + body
        + "os._exit(17)\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert proc.returncode == 17, proc.stderr.decode()
    files = find_log_files(tmp_path / "crash_log")
    assert files, "no log file written"
    return salvage_log_text(files[-1])


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
        ("xz", 0, 6, 1_000_000),
        # zstd: level 1 uses a 512 KiB window, level 19 an 8 MiB one
        ("zstd", 1, 19, 1_000_000),
    ],
)
def test_compression_level_takes_effect(tmp_path, backend, low, high, block):
    _skip_if_unavailable(backend)
    lines = _random_block(block)
    lines = lines + lines  # identical content, `block` bytes apart
    low_size, _ = _log_and_measure(tmp_path, f"lvl_{backend}_lo", lines, in_place_compression=backend, compression_level=low)
    high_size, _ = _log_and_measure(tmp_path, f"lvl_{backend}_hi", lines, in_place_compression=backend, compression_level=high)
    assert high_size < 0.75 * low_size
