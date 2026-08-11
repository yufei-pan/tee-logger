from Tee_Logger import abbreviate_filename
from log_utils import find_log_files, read_log_text


def test_abbreviate_short_name():
    assert abbreviate_filename("mod.py", 42)


def test_factory_writes_log(tee_factory, tmp_path):
    tl = tee_factory(programName="smoke_factory")
    tl.info("hello-smoke")
    files = find_log_files(tmp_path / "smoke_factory_log")
    assert files
    assert "hello-smoke" in read_log_text(files[-1])
