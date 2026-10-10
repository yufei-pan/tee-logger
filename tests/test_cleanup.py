import datetime
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

import Tee_Logger
from Tee_Logger import compress_folder
from log_utils import close_tee_logger, make_tee_logger

SRC = Path(__file__).resolve().parents[1] / "src"


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


def test_cleanup_does_not_rerun_unguarded_main_script(tmp_path):
    """Under spawn/forkserver a process pool re-imports __main__; cleanup must not."""
    logs_dir = tmp_path / "reimp_log"
    _seed_day(logs_dir, "2020-06-01")
    marker = tmp_path / "ran.txt"
    script = tmp_path / "unguarded.py"
    script.write_text(
        "import multiprocessing, sys\n"
        "multiprocessing.set_start_method('spawn', force=True)\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "from Tee_Logger import teeLogger\n"
        f"open({str(marker)!r}, 'a').write('ran\\n')\n"
        f"tl = teeLogger(systemLogFileDir={str(tmp_path)!r}, programName='reimp', suppressPrintout=True, "
        "compressLogAfterMonths=1, deleteLogAfterYears=0)\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode()
    assert marker.read_text() == "ran\n"
    assert (logs_dir / "2020-06-01.tar.xz").exists()


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


def test_cleanup_ages_folders_by_name_not_ctime(tmp_path, monkeypatch):
    monkeypatch.delattr(Tee_Logger, "dateutil", raising=False)
    name = "by_name"
    logs_dir = tmp_path / f"{name}_log"
    tl = make_tee_logger(tmp_path, programName=name, compressLogAfterMonths=1)
    _seed_day(logs_dir, "2020-06-01")  # mtime backdated, but ctime is now (like a restored backup)
    tl.cleanup_old_logs()
    assert (logs_dir / "2020-06-01.tar.xz").exists()
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
