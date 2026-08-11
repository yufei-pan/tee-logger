import os
import sys

from Tee_Logger import getCallerInfo


def test_auto_mode_from_user_wrapper(tee_factory, tmp_path):
    tl = tee_factory(programName="caller_auto")

    def my_wrapper(msg):
        log_at = sys._getframe().f_lineno + 1
        tl.info(msg)
        return log_at

    def business():
        return my_wrapper("wrapped message")

    log_at = business()
    from log_utils import find_log_files, read_log_text

    content = read_log_text(find_log_files(tmp_path / "caller_auto_log")[-1])
    prefix = content.split("wrapped message")[0]
    assert f":{log_at}" in prefix
    assert "Tee_Logger" not in prefix.split("\n")[-1]


def test_auto_mode_direct_call(tee_factory, tmp_path):
    tl = tee_factory(programName="caller_direct")
    log_at = sys._getframe().f_lineno + 1
    tl.info("direct message")
    from log_utils import find_log_files, read_log_text

    content = read_log_text(find_log_files(tmp_path / "caller_direct_log")[-1])
    assert "direct message" in content
    assert f":{log_at}" in content


def test_explicit_positive_depth():
    filename, _lineno = getCallerInfo(1)
    assert filename == os.path.basename(__file__)


def test_per_call_stack_depth_override(tee_factory, tmp_path):
    tl = tee_factory(programName="caller_override", callerStackDepth=-1)

    def inner():
        # Force a deeper explicit depth if meaningful; at minimum ensure override is accepted
        tl.info("override-msg", callerStackDepth=0)

    inner()
    from log_utils import find_log_files, read_log_text

    content = read_log_text(find_log_files(tmp_path / "caller_override_log")[-1])
    assert "override-msg" in content
