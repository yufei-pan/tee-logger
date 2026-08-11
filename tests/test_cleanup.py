import time
from pathlib import Path
from unittest import mock

from Tee_Logger import compress_folder
from log_utils import close_tee_logger, make_tee_logger


def _seed_day(logs_dir: Path, day: str, text: str = "old"):
    day_dir = logs_dir / day
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"sample_{day}.log").write_text(text + "\n", encoding="utf-8")
    return day_dir


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
