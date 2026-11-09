import datetime
import errno
import os
import shutil
import stat
import tarfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

import Tee_Logger
from Tee_Logger import compress_folder
from log_utils import close_tee_logger, make_tee_logger

def _backdate(path: Path, day: str):
    stamp = datetime.datetime.strptime(day, "%Y-%m-%d").timestamp() + 12 * 3600
    os.utime(path, (stamp, stamp))


def _seed_day(logs_dir: Path, day: str, text: str = "old", fresh: bool = False):
    """Create a day folder whose file looks like it was last written on that day."""
    day_dir = logs_dir / day
    day_dir.mkdir(parents=True, exist_ok=True)
    log = day_dir / f"sample_{day}.log"
    log.write_text(text + "\n", encoding="utf-8")
    if not fresh:
        _backdate(log, day)
        _backdate(day_dir, day)
    return day_dir


def _archive_members(archive: Path):
    with tarfile.open(archive, "r:xz") as tar:
        return {m.name: (tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers()}


def _wait_until(predicate, timeout=2.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_delete_old_year_folders(tmp_path):
    name = "clean_del"
    tl = make_tee_logger(
        tmp_path,
        programName=name,
        deleteLogAfterYears=1,
        compressLogAfterMonths=0,
    )
    logs_dir = tmp_path / f"{name}_log"
    _seed_day(logs_dir, "2000-01-01")
    _seed_day(logs_dir, "2099-01-01")  # far future keeps; or use today
    tl.cleanup_old_logs()
    assert _wait_until(lambda: not (logs_dir / "2000-01-01").exists())
    assert (logs_dir / "2099-01-01").exists()
    close_tee_logger(tl)


def test_compress_old_month_folders(tmp_path):
    name = "clean_cmp"
    tl = make_tee_logger(
        tmp_path,
        programName=name,
        deleteLogAfterYears=0,
        compressLogAfterMonths=1,
    )
    logs_dir = tmp_path / f"{name}_log"
    # ~1 year old day folder name triggers compress when months=1
    _seed_day(logs_dir, "2020-06-01")
    tl.cleanup_old_logs()
    assert _wait_until(lambda: not (logs_dir / "2020-06-01").exists())
    assert (logs_dir / "2020-06-01.tar.xz").exists()
    close_tee_logger(tl)


def test_maintenance_disabled_with_zero(tmp_path):
    name = "clean_off"
    tl = make_tee_logger(
        tmp_path,
        programName=name,
        deleteLogAfterYears=0,
        compressLogAfterMonths=0,
    )
    logs_dir = tmp_path / f"{name}_log"
    _seed_day(logs_dir, "2000-01-01")
    tl.cleanup_old_logs()
    assert (logs_dir / "2000-01-01").exists()
    close_tee_logger(tl)


def test_compress_folder_tarfile_fallback(tmp_path):
    folder = tmp_path / "2019-01-01"
    folder.mkdir()
    (folder / "a.log").write_text("x\n", encoding="utf-8")
    with mock.patch("Tee_Logger.shutil.which", return_value=None):
        assert compress_folder(str(folder), disable_colors=True) is True
    assert not folder.exists()
    assert (tmp_path / "2019-01-01.tar.xz").exists()


def test_constructor_does_not_wait_for_cleanup(tmp_path, monkeypatch):
    logs_dir = tmp_path / "bg_log"
    _seed_day(logs_dir, "2020-06-01")
    started, release = threading.Event(), threading.Event()

    def slow_compress(path, **kwargs):
        started.set()
        release.wait(10)
        return True

    monkeypatch.setattr(Tee_Logger, "compress_folder", slow_compress)
    tl = make_tee_logger(tmp_path, programName="bg", compressLogAfterMonths=1)
    try:
        assert started.wait(5), "cleanup never ran in this process"
    finally:
        release.set()
        close_tee_logger(tl)


def test_cleanup_failure_is_reported(tmp_path, monkeypatch, capsys):
    logs_dir = tmp_path / "rep_log"
    _seed_day(logs_dir, "2000-01-01")
    tl = make_tee_logger(tmp_path, programName="rep")

    def broken_rmtree(path, *a, **k):
        raise PermissionError("simulated")

    monkeypatch.setattr(Tee_Logger.shutil, "rmtree", broken_rmtree)
    tl.deleteLogAfterYears = 1
    tl.cleanup_old_logs()
    assert "simulated" in capsys.readouterr().err
    close_tee_logger(tl)


@pytest.mark.skipif(os.name == "nt", reason="fake tar is a shell script")
def test_compress_folder_survives_failing_tar(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_tar = bin_dir / "tar"
    # Behaves like GNU tar dying halfway: honours --remove-files, then fails.
    fake_tar.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do [ \"$a\" = --remove-files ] && rm -rf ./*/*; done\n"
        "echo 'tar: simulated failure' >&2\n"
        "exit 2\n"
    )
    fake_tar.chmod(0o755)
    (bin_dir / "xz").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "xz").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    logs = tmp_path / "logs"
    folder = _seed_day(logs, "2019-01-01", text="precious")

    assert compress_folder(str(folder), disable_colors=True) is True
    assert not folder.exists()
    members = _archive_members(logs / "2019-01-01.tar.xz")
    assert members["2019-01-01/sample_2019-01-01.log"] == b"precious\n"
    assert sorted(p.name for p in logs.iterdir()) == ["2019-01-01.tar.xz"]


def test_compress_folder_missing_folder_writes_nothing(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    assert compress_folder(str(logs / "2019-01-01"), disable_colors=True) is False
    assert list(logs.iterdir()) == []


def test_cleanup_resumes_stale_compression_claim(tmp_path):
    name = "claim_stale"
    logs_dir = tmp_path / f"{name}_log"
    tl = make_tee_logger(tmp_path, programName=name, compressLogAfterMonths=1)
    claim = logs_dir / "2020-06-01.compressing-99999"
    _seed_day(claim, "2020-06-01", text="half-done")
    _backdate(claim, "2020-06-01")
    tl.cleanup_old_logs()
    assert not claim.exists()
    members = _archive_members(logs_dir / "2020-06-01.tar.xz")
    assert members["2020-06-01/sample_2020-06-01.log"] == b"half-done\n"
    close_tee_logger(tl)


def test_cleanup_leaves_fresh_claim_alone(tmp_path):
    name = "claim_fresh"
    logs_dir = tmp_path / f"{name}_log"
    tl = make_tee_logger(tmp_path, programName=name, compressLogAfterMonths=1, deleteLogAfterYears=1)
    claim = logs_dir / "2020-06-01.compressing-99999"
    _seed_day(claim, "2020-06-01")  # claim dir itself keeps a fresh mtime: someone is working on it
    tl.cleanup_old_logs()
    assert (claim / "2020-06-01" / "sample_2020-06-01.log").exists()
    assert not (logs_dir / "2020-06-01.tar.xz").exists()
    close_tee_logger(tl)


def test_cleanup_finishes_stale_deletion(tmp_path):
    name = "del_stale"
    logs_dir = tmp_path / f"{name}_log"
    tl = make_tee_logger(tmp_path, programName=name, compressLogAfterMonths=1)
    leftover = logs_dir / "2020-06-01.deleting-99999"
    _seed_day(leftover, "2020-06-01")
    _backdate(leftover, "2020-06-01")
    tl.cleanup_old_logs()
    assert not leftover.exists()
    close_tee_logger(tl)


def test_cleanup_ages_folders_by_name_not_ctime(tmp_path):
    name = "by_name"
    logs_dir = tmp_path / f"{name}_log"
    tl = make_tee_logger(tmp_path, programName=name, compressLogAfterMonths=1)
    _seed_day(logs_dir, "2020-06-01")  # mtime backdated, but ctime is now (like a restored backup)
    tl.cleanup_old_logs()
    assert (logs_dir / "2020-06-01.tar.xz").exists()
    close_tee_logger(tl)


def test_cleanup_skips_a_folder_named_after_no_real_date(tmp_path, capsys):
    name = "bad_date"
    logs_dir = tmp_path / f"{name}_log"
    tl = make_tee_logger(tmp_path, programName=name, compressLogAfterMonths=1)
    _seed_day(logs_dir, "2020-13-45", fresh=True)  # its age can only come from its name
    _seed_day(logs_dir, "2020-06-01")
    tl.cleanup_old_logs()
    assert (logs_dir / "2020-13-45").is_dir()
    assert (logs_dir / "2020-06-01.tar.xz").exists()  # the rest is still cleaned up
    assert "2020-13-45" in capsys.readouterr().err
    close_tee_logger(tl)


@pytest.mark.parametrize("setting", ["compressLogAfterMonths", "deleteLogAfterYears"])
def test_cleanup_skips_recently_written_folder(tmp_path, setting):
    name = "busy_" + setting
    logs_dir = tmp_path / f"{name}_log"
    tl = make_tee_logger(tmp_path, programName=name, **{setting: 1})
    _seed_day(logs_dir, "2020-06-01", text="still being written", fresh=True)
    tl.cleanup_old_logs()
    assert (logs_dir / "2020-06-01" / "sample_2020-06-01.log").read_text() == "still being written\n"
    assert not (logs_dir / "2020-06-01.tar.xz").exists()
    close_tee_logger(tl)


# --- the archive is on disk before the logs go (6.45) ---

def _record_compression(monkeypatch, fsync_errors=None):
    """Record fsync, replace, the rename for deletion and rmtree, still running each for real.

    ``fsync_errors`` maps "file" or "dir" to an errno that kind of fsync raises instead.
    """
    import shutil
    events = []
    real_fsync, real_replace, real_rename, real_rmtree = os.fsync, os.replace, os.rename, shutil.rmtree

    def fsync(fd):
        kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        events.append("fsync-" + kind)
        if fsync_errors and kind in fsync_errors:
            raise OSError(fsync_errors[kind], os.strerror(fsync_errors[kind]))
        return real_fsync(fd)

    def replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    def rename(src, dst):
        if ".deleting-" in str(dst):
            events.append("rename-to-deleting")
        return real_rename(src, dst)

    def rmtree(path, *args, **kwargs):
        events.append("rmtree")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Tee_Logger.os, "fsync", fsync)
    monkeypatch.setattr(Tee_Logger.os, "replace", replace)
    monkeypatch.setattr(Tee_Logger.os, "rename", rename)
    monkeypatch.setattr(Tee_Logger.shutil, "rmtree", rmtree)
    return events


@pytest.fixture(params=["tar-cli", "tarfile"])
def tar_backend(request, monkeypatch):
    if request.param == "tarfile":
        monkeypatch.setattr(Tee_Logger.shutil, "which", lambda name: None)
    elif os.name == "nt" or not (shutil.which("tar") and shutil.which("xz")):
        pytest.skip("no tar and xz on PATH")
    return request.param


def test_compress_folder_syncs_archive_before_replace_and_directory_before_removal(tmp_path, monkeypatch, tar_backend):
    logs = tmp_path / "logs"
    folder = _seed_day(logs, "2019-01-01", text="precious")
    events = _record_compression(monkeypatch)
    assert compress_folder(str(folder), disable_colors=True) is True
    assert events[:5] == ["fsync-file", "replace", "fsync-dir", "rename-to-deleting", "rmtree"]
    assert events.count("fsync-file") == 1 and events.count("fsync-dir") == 1
    members = _archive_members(logs / "2019-01-01.tar.xz")
    assert members["2019-01-01/sample_2019-01-01.log"] == b"precious\n"
    assert sorted(p.name for p in logs.iterdir()) == ["2019-01-01.tar.xz"]


def test_compress_folder_archive_fsync_error_puts_the_folder_back(tmp_path, monkeypatch, tar_backend, capsys):
    logs = tmp_path / "logs"
    folder = _seed_day(logs, "2019-01-01", text="precious")
    events = _record_compression(monkeypatch, {"file": errno.EIO})
    assert compress_folder(str(folder), disable_colors=True) is False
    assert "replace" not in events and "rename-to-deleting" not in events
    assert (folder / "sample_2019-01-01.log").read_text() == "precious\n"
    assert sorted(p.name for p in logs.iterdir()) == ["2019-01-01"]  # no archive, no claim, no temp file
    assert "Failed to sync the archive" in capsys.readouterr().err


def test_compress_folder_directory_fsync_error_keeps_the_logs_and_a_later_run_finishes(tmp_path, monkeypatch, tar_backend, capsys):
    logs = tmp_path / "logs"
    folder = _seed_day(logs, "2019-01-01", text="precious")
    events = _record_compression(monkeypatch, {"dir": errno.EIO})
    assert compress_folder(str(folder), disable_colors=True) is False
    assert "rename-to-deleting" not in events and "rmtree" not in events
    claim = logs / f"2019-01-01.compressing-{os.getpid()}"
    assert (claim / "2019-01-01" / "sample_2019-01-01.log").read_text() == "precious\n"
    assert "Failed to compress folder" in capsys.readouterr().err
    monkeypatch.undo()
    # a later cleanup takes over the claim (what a stale one gets) and finishes
    assert compress_folder(str(claim), disable_colors=True) is True
    members = _archive_members(logs / "2019-01-01.tar.xz")
    assert members["2019-01-01/sample_2019-01-01.log"] == b"precious\n"
    assert sorted(p.name for p in logs.iterdir()) == ["2019-01-01.tar.xz"]


@pytest.mark.parametrize("code", [errno.EINVAL, errno.ENOSYS, getattr(errno, "ENOTSUP", errno.EINVAL)])
def test_compress_folder_carries_on_where_fsync_is_unsupported(tmp_path, monkeypatch, tar_backend, code):
    logs = tmp_path / "logs"
    folder = _seed_day(logs, "2019-01-01", text="precious")
    events = _record_compression(monkeypatch, {"file": code, "dir": code})
    assert compress_folder(str(folder), disable_colors=True) is True
    assert events[:5] == ["fsync-file", "replace", "fsync-dir", "rename-to-deleting", "rmtree"]
    assert sorted(p.name for p in logs.iterdir()) == ["2019-01-01.tar.xz"]


def test_compress_folder_carries_on_where_a_directory_cannot_be_opened(tmp_path, monkeypatch, tar_backend):
    logs = tmp_path / "logs"
    folder = _seed_day(logs, "2019-01-01", text="precious")
    real_open = os.open

    def open_(path, flags, *args, **kwargs):
        if os.fspath(path) == str(logs):  # as os.open of a directory fails on Windows
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(Tee_Logger.os, "open", open_)
    assert compress_folder(str(folder), disable_colors=True) is True
    assert sorted(p.name for p in logs.iterdir()) == ["2019-01-01.tar.xz"]
