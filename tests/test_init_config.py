import getpass
import logging
import os
import shutil
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text


def test_nolog_uses_null_handler(tmp_path):
    tl = make_tee_logger(tmp_path, programName="nolog", noLog=True)
    assert tl.noLog is True
    assert any(isinstance(h, logging.NullHandler) for h in tl.logger.handlers)
    tl.info("silent")
    assert not (tmp_path / "nolog_log").exists()
    close_tee_logger(tl)


@pytest.mark.parametrize("dev", ["/dev/null", "/dev/stdout", "/dev/stderr"])
def test_dev_paths_force_nolog(tmp_path, dev):
    tl = make_tee_logger(tmp_path, programName="devpath", systemLogFileDir=dev)
    assert tl.noLog is True
    close_tee_logger(tl)


def _block_imports(*blocked):
    """Make importing the given modules fail, as in a Python built without them."""
    return mock.patch.dict(sys.modules, {name: None for name in blocked})


def test_inplace_true_prefers_zstd(tmp_path):
    pytest.importorskip("compression.zstd")
    tl = make_tee_logger(tmp_path, programName="comp_true", in_place_compression=True)
    assert tl.in_place_compression == "zstd"
    assert str(tl.logFileName).endswith(".zst")
    close_tee_logger(tl)


@pytest.mark.parametrize(
    "blocked,expected,suffix",
    [
        (["compression.zstd"], "xz", ".xz"),
        (["compression.zstd", "lzma"], "gzip", ".gz"),
        (["compression.zstd", "lzma", "gzip"], "bz2", ".bz2"),
        (["compression.zstd", "lzma", "gzip", "bz2"], None, ".log"),
    ],
)
def test_inplace_true_walks_fallback_chain(tmp_path, blocked, expected, suffix):
    with _block_imports(*blocked):
        tl = make_tee_logger(tmp_path, programName="comp_chain", in_place_compression=True)
    assert tl.in_place_compression == expected
    assert str(tl.logFileName).endswith(suffix)
    close_tee_logger(tl)


def test_explicit_backend_unavailable_falls_to_next_in_chain(tmp_path):
    with _block_imports("lzma"):
        tl = make_tee_logger(tmp_path, programName="comp_xz_missing", in_place_compression="xz")
    assert tl.in_place_compression == "gzip"
    close_tee_logger(tl)


def test_invalid_compression_uses_default_chain(tmp_path):
    with _block_imports("compression.zstd"):
        tl = make_tee_logger(tmp_path, programName="comp_bad", in_place_compression="nope")
    assert tl.in_place_compression == "xz"
    tl.info("still-works")
    close_tee_logger(tl)
    content = read_log_text(find_log_files(tmp_path / "comp_bad_log")[-1])
    assert "still-works" in content


def test_collapse_defaults_true_when_compressing(tmp_path):
    tl = make_tee_logger(tmp_path, programName="collapse_on", in_place_compression="gzip")
    assert tl.collapse_single_day_logs is True
    close_tee_logger(tl)


def test_collapse_defaults_false_without_compression(tmp_path):
    tl = make_tee_logger(tmp_path, programName="collapse_off")
    assert tl.collapse_single_day_logs is False
    close_tee_logger(tl)


def _private_dir_name():
    return "teeLogger-" + (str(os.getuid()) if hasattr(os, "getuid") else getpass.getuser())


def _fallback_setup(tmp_path, monkeypatch):
    """Make the primary log dir impossible (a file where a parent dir should be) and
    point the system temp dir at tmp_path/shared."""
    import Tee_Logger

    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(Tee_Logger.tempfile, "gettempdir", lambda: str(shared))
    return str(blocker / "logs"), shared


def test_unwritable_primary_falls_back_to_private_tempdir(tmp_path, monkeypatch, capsys):
    primary, shared = _fallback_setup(tmp_path, monkeypatch)
    tl = make_tee_logger(tmp_path, programName="fallback", systemLogFileDir=primary)
    tl.info("fallback-msg")
    close_tee_logger(tl)
    private = shared / _private_dir_name()
    assert tl.systemLogFileDir == str(private)
    if os.name != "nt":
        assert stat.S_IMODE(private.stat().st_mode) & 0o077 == 0
    assert "fallback-msg" in read_log_text(find_log_files(private / "fallback_log")[-1])
    captured = capsys.readouterr()
    assert "Failed to create log file" in captured.err  # reported even though printout is suppressed
    assert "Failed to create log file" not in captured.out


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_fallback_tightens_loose_permissions_on_own_dir(tmp_path, monkeypatch):
    primary, shared = _fallback_setup(tmp_path, monkeypatch)
    private = shared / _private_dir_name()
    private.mkdir()
    private.chmod(0o777)
    tl = make_tee_logger(tmp_path, programName="fb_loose", systemLogFileDir=primary)
    close_tee_logger(tl)
    assert tl.systemLogFileDir == str(private)
    assert stat.S_IMODE(private.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="symlinks not asserted on Windows")
def test_fallback_refuses_planted_symlink_dir(tmp_path, monkeypatch):
    primary, shared = _fallback_setup(tmp_path, monkeypatch)
    elsewhere = tmp_path / "attacker_target"
    elsewhere.mkdir()
    (shared / _private_dir_name()).symlink_to(elsewhere)
    tl = make_tee_logger(tmp_path, programName="fb_link", systemLogFileDir=primary)
    tl.info("must-not-land-in-target")
    close_tee_logger(tl)
    assert list(elsewhere.iterdir()) == []
    assert not str(tl.logFileName).startswith(str(shared))


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="needs root to chown")
def test_fallback_refuses_dir_owned_by_another_user(tmp_path, monkeypatch):
    primary, shared = _fallback_setup(tmp_path, monkeypatch)
    private = shared / _private_dir_name()
    private.mkdir(mode=0o700)
    os.chown(str(private), 65534, 65534)
    tl = make_tee_logger(tmp_path, programName="fb_foreign", systemLogFileDir=primary)
    tl.info("must-not-land-in-foreign-dir")
    close_tee_logger(tl)
    assert list(private.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="symlinks not asserted on Windows")
@pytest.mark.parametrize("backend", [None, "gzip", "bz2", "xz", "zstd"])
def test_log_file_open_does_not_follow_planted_symlink(tmp_path, monkeypatch, backend):
    if backend == "zstd":
        pytest.importorskip("compression.zstd")
    import datetime

    shared = tmp_path / "shared"
    shared.mkdir()
    import Tee_Logger

    monkeypatch.setattr(Tee_Logger.tempfile, "gettempdir", lambda: str(shared))
    victim = tmp_path / "victim.txt"
    victim.write_text("original\n")
    day = datetime.date.today().isoformat()
    suffix = {None: "", "gzip": ".gz", "bz2": ".bz2", "xz": ".xz", "zstd": ".zst"}[backend]
    planted = tmp_path / "plant_log" / day / ("plant_" + day + ".log" + suffix)
    planted.parent.mkdir(parents=True)
    planted.symlink_to(victim)
    tl = make_tee_logger(tmp_path, programName="plant", in_place_compression=backend, collapse_single_day_logs=True)
    tl.warning("must-not-reach-victim")
    close_tee_logger(tl)
    assert victim.read_bytes() == b"original\n"
    assert planted.is_symlink()


def test_banner_goes_to_stderr(tmp_path, capsys):
    tl = make_tee_logger(tmp_path, programName="banner_on", suppressPrintout=False)
    captured = capsys.readouterr()
    assert "Log file: " + tl.logFileName in captured.err
    assert "Log file" not in captured.out
    close_tee_logger(tl)


def test_banner_respects_suppress_printout(tmp_path, capsys):
    tl = make_tee_logger(tmp_path, programName="banner_off", suppressPrintout=True)
    captured = capsys.readouterr()
    assert "Log file" not in captured.out + captured.err
    close_tee_logger(tl)


def test_tee_output_has_no_ansi_codes_when_not_a_tty(tmp_path, capsys):
    tl = make_tee_logger(tmp_path, programName="no_ansi", suppressPrintout=False, disable_colors=...)
    tl.teeprint("plain-text")
    assert "\x1b[" not in capsys.readouterr().out
    close_tee_logger(tl)


def test_default_program_name_drops_extension(tmp_path):
    from Tee_Logger import teeLogger

    tl = teeLogger(systemLogFileDir=str(tmp_path), suppressPrintout=True, compressLogAfterMonths=0, deleteLogAfterYears=0)
    assert tl.name == "test_init_config"
    assert (tmp_path / "test_init_config_log").is_dir()
    close_tee_logger(tl)
