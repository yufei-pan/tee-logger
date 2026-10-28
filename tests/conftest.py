import pytest

from log_utils import close_tee_logger, make_tee_logger


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
