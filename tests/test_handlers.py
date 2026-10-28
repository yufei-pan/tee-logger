import logging
import os
from pathlib import Path

import pytest

from Tee_Logger import teeLogger
from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text


def test_same_program_name_replaces_file_handler(tmp_path):
    tl1 = make_tee_logger(tmp_path, programName="dup_handlers")
    tl2 = make_tee_logger(tmp_path, programName="dup_handlers")
    logger = logging.getLogger("dup_handlers")
    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
    tl1.info("once")
    tl2.info("twice")
    content = read_log_text(find_log_files(tmp_path / "dup_handlers_log")[-1])
    assert content.count("once") == 1
    assert content.count("twice") == 1
    close_tee_logger(tl1)
    close_tee_logger(tl2)


@pytest.mark.skipif(os.name == "nt", reason="symlinks not asserted on Windows")
def test_latest_symlink_points_at_active_log(tmp_path):
    tl = make_tee_logger(tmp_path, programName="symlink_me")
    latest = tmp_path / "symlink_me_log" / "symlink_me_latest.log"
    assert latest.is_symlink()
    target = Path(os.path.join(latest.parent, os.readlink(latest))).resolve()
    assert target.exists()
    assert "symlink_me" in target.name
    close_tee_logger(tl)


@pytest.mark.skipif(os.name == "nt", reason="symlinks not asserted on Windows")
def test_symlink_failure_keeps_logging_in_place(tmp_path, monkeypatch, capsys):
    def no_symlinks(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "symlink", no_symlinks)
    tl = make_tee_logger(tmp_path, programName="nolink")
    tl.info("kept-here")
    assert str(tl.logFileName).startswith(str(tmp_path))
    assert "kept-here" in read_log_text(find_log_files(tmp_path / "nolink_log")[-1])
    assert "nolink_latest.log" in capsys.readouterr().err
    close_tee_logger(tl)


@pytest.mark.skipif(os.name == "nt", reason="symlinks not asserted on Windows")
def test_latest_link_switches_suffix_and_drops_stale_one(tmp_path):
    first = make_tee_logger(tmp_path, programName="relink", in_place_compression="gzip")
    close_tee_logger(first)
    second = make_tee_logger(tmp_path, programName="relink")
    logs = tmp_path / "relink_log"
    assert not os.path.lexists(logs / "relink_latest.log.gz")
    target = Path(os.path.join(logs, os.readlink(logs / "relink_latest.log")))
    assert target.resolve() == Path(second.logFileName).resolve()
    close_tee_logger(second)


@pytest.mark.parametrize("backend", [None, "gzip"])
def test_rollover_at_midnight_moves_to_new_day(tmp_path, monkeypatch, backend):
    import datetime
    import time

    tl = make_tee_logger(tmp_path, programName="roll", in_place_compression=backend)
    tl.info("before-midnight")
    first_file = Path(tl.logFileName)
    handlers = list(tl.logger.handlers)
    real_time = time.time
    later = real_time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    tl.info("after-midnight")
    monkeypatch.setattr(time, "time", real_time)
    second_file = Path(tl.logFileName)
    # the same handler, now writing the new file
    assert tl.logger.handlers == handlers
    (ours,) = [h for h in handlers if isinstance(h, logging.FileHandler)]
    assert ours.baseFilename == str(second_file)
    close_tee_logger(tl)

    new_day = datetime.date.fromtimestamp(later).isoformat()
    assert second_file.parent.name == new_day
    assert first_file != second_file
    old_text, new_text = read_log_text(first_file), read_log_text(second_file)
    assert "before-midnight" in old_text and "after-midnight" not in old_text
    assert "after-midnight" in new_text
    if os.name != "nt":
        suffix = "" if backend is None else ".gz"
        latest = tmp_path / "roll_log" / ("roll_latest.log" + suffix)
        assert Path(os.path.join(latest.parent, os.readlink(latest))).resolve() == second_file.resolve()


def test_a_users_own_file_handler_is_kept(tmp_path):
    first = make_tee_logger(tmp_path, programName="keep_mine")
    mine = logging.FileHandler(str(tmp_path / "mine.log"))
    first.logger.addHandler(mine)
    second = make_tee_logger(tmp_path, programName="keep_mine")
    handlers = list(second.logger.handlers)
    second.logger.removeHandler(mine)
    mine.close()
    close_tee_logger(second)
    assert mine in handlers and len(handlers) == 2


def test_midnight_switches_teeloggers_handler_not_a_users_own(tmp_path, monkeypatch):
    import time

    from Tee_Logger import teeLogger as TL

    mine_path = tmp_path / "mine.log"
    mine = TL.BinFileHandler(str(mine_path), "ab", "utf-8")  # a user's own, before the teeLogger
    logging.getLogger("not_mine").addHandler(mine)
    tl = make_tee_logger(tmp_path, programName="not_mine")
    first = tl.logFileName
    later = time.time() + 2 * 86400
    monkeypatch.setattr(time, "time", lambda: later)
    tl.info("after-midnight")
    monkeypatch.undo()
    second = tl.logFileName
    tl.logger.removeHandler(mine)
    mine.close()
    close_tee_logger(tl)
    assert mine.baseFilename == str(mine_path)
    assert second != first and "after-midnight" in read_log_text(Path(second))


def test_nolog_twice_keeps_one_null_handler(tmp_path):
    make_tee_logger(tmp_path, programName="nolog_twice", noLog=True)
    tl = make_tee_logger(tmp_path, programName="nolog_twice", noLog=True)
    assert sum(isinstance(h, logging.NullHandler) for h in tl.logger.handlers) == 1
    close_tee_logger(tl)


def test_stderr_fallback_handler_is_replaced_on_reinit(tmp_path, monkeypatch, capsys):
    import Tee_Logger

    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    monkeypatch.setattr(Tee_Logger.tempfile, "gettempdir", lambda: str(blocker / "tmp"))
    make_tee_logger(tmp_path, programName="leak", systemLogFileDir=str(blocker / "logs"))
    monkeypatch.undo()
    tl = make_tee_logger(tmp_path, programName="leak")
    capsys.readouterr()
    tl.info("only-in-file")
    assert "only-in-file" not in capsys.readouterr().err
    assert all(isinstance(h, logging.FileHandler) for h in tl.logger.handlers)
    close_tee_logger(tl)
