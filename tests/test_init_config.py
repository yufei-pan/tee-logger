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


def _block_imports(*blocked):
    """Make ``import X`` / ``from X import Y`` fail for the given module names."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        full = {name} | {name + "." + f for f in (fromlist or ())}
        if full & set(blocked):
            raise ImportError("blocked for test: " + name)
        return real_import(name, globals, locals, fromlist, level)

    return mock.patch("builtins.__import__", side_effect=fake_import)


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
        (["compression.zstd", "lzma", "zlib"], "bz2", ".bz2"),
        (["compression.zstd", "lzma", "zlib", "bz2"], None, ".log"),
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


def test_unwritable_primary_falls_back_to_tempdir(tmp_path, monkeypatch, capsys):
    import Tee_Logger

    # A regular file where a parent directory should be: unwritable even for root.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    fallback_root = tmp_path / "tmpdir"
    fallback_root.mkdir()
    monkeypatch.setattr(Tee_Logger.tempfile, "gettempdir", lambda: str(fallback_root))
    tl = make_tee_logger(tmp_path, programName="fallback", systemLogFileDir=str(blocker / "logs"))
    tl.info("fallback-msg")
    close_tee_logger(tl)
    assert tl.systemLogFileDir == str(fallback_root)
    assert "fallback-msg" in read_log_text(find_log_files(fallback_root / "fallback_log")[-1])
    captured = capsys.readouterr()
    assert "Failed to create log file" in captured.err  # reported even though printout is suppressed
    assert "Failed to create log file" not in captured.out


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
