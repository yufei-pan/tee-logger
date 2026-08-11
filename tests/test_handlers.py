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


def test_gzip_respects_binary_mode_false(tmp_path):
    tl = make_tee_logger(
        tmp_path,
        programName="gzip_text",
        in_place_compression="gzip",
        binary_mode=False,
    )
    handler = tl.logger.handlers[0]
    assert handler.mode == "at"
    close_tee_logger(tl)


@pytest.mark.skipif(os.name == "nt", reason="symlinks not asserted on Windows")
def test_latest_symlink_points_at_active_log(tmp_path):
    tl = make_tee_logger(tmp_path, programName="symlink_me")
    latest = tmp_path / "symlink_me_log" / "symlink_me_latest.log"
    assert latest.is_symlink()
    target = Path(os.path.join(latest.parent, os.readlink(latest))).resolve()
    assert target.exists()
    assert "symlink_me" in target.name
    close_tee_logger(tl)
