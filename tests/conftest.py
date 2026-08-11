import pytest

from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text, unique_name


@pytest.fixture
def program_name():
    return unique_name()


@pytest.fixture
def tee_factory(tmp_path):
    created = []

    def _factory(**kwargs):
        tl = make_tee_logger(tmp_path, **kwargs)
        created.append(tl)
        return tl

    yield _factory
    for tl in created:
        close_tee_logger(tl)


@pytest.fixture
def read_active_log(tmp_path, program_name):
    def _read(name=None):
        root = tmp_path / f"{name or program_name}_log"
        files = find_log_files(root)
        assert files, f"no log files under {root}"
        return read_log_text(files[-1])

    return _read
