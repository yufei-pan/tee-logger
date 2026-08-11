import logging
import shutil
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


def test_inplace_true_means_xz(tmp_path):
    tl = make_tee_logger(tmp_path, programName="comp_true", in_place_compression=True)
    assert tl.in_place_compression == "xz"
    assert str(tl.logFileName).endswith(".xz")
    close_tee_logger(tl)


def test_invalid_compression_falls_back_to_xz(tmp_path):
    tl = make_tee_logger(tmp_path, programName="comp_bad", in_place_compression="nope")
    assert tl.in_place_compression == "xz"
    tl.info("still-works")
    # xz needs the stream closed before lzma can decompress
    close_tee_logger(tl)
    content = read_log_text(find_log_files(tmp_path / "comp_bad_log")[-1])
    assert "still-works" in content


def test_zstd_fallback_when_import_fails(tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        # Only fail the constructor's `from compression import zstd` path.
        if name == "compression.zstd" or (
            name == "compression" and fromlist and "zstd" in fromlist
        ):
            raise ImportError("forced")
        return real_import(name, globals, locals, fromlist, level)

    with mock.patch("builtins.__import__", side_effect=fake_import):
        tl = make_tee_logger(tmp_path, programName="zstd_fb", in_place_compression="zstd")
    assert tl.in_place_compression == "xz"
    close_tee_logger(tl)


def test_collapse_defaults_true_when_compressing(tmp_path):
    tl = make_tee_logger(tmp_path, programName="collapse_on", in_place_compression="gzip")
    assert tl.collapse_single_day_logs is True
    close_tee_logger(tl)


def test_collapse_defaults_false_without_compression(tmp_path):
    tl = make_tee_logger(tmp_path, programName="collapse_off")
    assert tl.collapse_single_day_logs is False
    close_tee_logger(tl)


def test_unwritable_primary_falls_back_to_tmp():
    name = "fallback_tmp_unique_9f3a"
    log_root = Path("/tmp") / f"{name}_log"
    if log_root.exists():
        shutil.rmtree(log_root)
    blocked = Path("/tmp") / "tee_logger_blocked_root_9f3a"
    if blocked.exists():
        shutil.rmtree(blocked)
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        # Point at a subdirectory we cannot create
        primary = blocked / "not_allowed"
        tl = make_tee_logger(primary, programName=name, systemLogFileDir=str(primary))
        # After failure, implementation sets systemLogFileDir to /tmp
        assert tl.systemLogFileDir == "/tmp" or (Path("/tmp") / f"{name}_log").exists()
        tl.info("fallback-msg")
        content = read_log_text(find_log_files(Path("/tmp") / f"{name}_log")[-1])
        assert "fallback-msg" in content
        close_tee_logger(tl)
    finally:
        blocked.chmod(0o700)
        shutil.rmtree(blocked, ignore_errors=True)
        shutil.rmtree(log_root, ignore_errors=True)
