import re

import pytest

from log_utils import close_tee_logger, find_log_files, make_tee_logger, read_log_text

BACKENDS = [None, "gzip", "bz2", "xz", "zstd"]
TIME_IN_NAME = re.compile(r"_\d{2}-\d{2}-\d{2}(?:\.|$)")


def _suffix_for(backend):
    return {None: ".log", "gzip": ".gz", "bz2": ".bz2", "xz": ".xz", "zstd": ".zst"}[backend]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("binary_mode", [True, False])
@pytest.mark.parametrize("collapse", [True, False, "default"])
def test_compression_roundtrip(tmp_path, backend, binary_mode, collapse):
    if backend == "zstd":
        pytest.importorskip("compression.zstd")
    kwargs = {
        "programName": f"cmp_{backend}_{binary_mode}_{collapse}".replace(".", "p"),
        "binary_mode": binary_mode,
        "in_place_compression": backend,
    }
    if collapse != "default":
        kwargs["collapse_single_day_logs"] = collapse
    tl = make_tee_logger(tmp_path, **kwargs)
    msg = f"payload-{backend}-{binary_mode}-{collapse}"
    tl.info(msg)
    from pathlib import Path

    path = Path(tl.logFileName)
    collapsed = tl.collapse_single_day_logs
    close_tee_logger(tl)

    assert path.exists()
    assert path.name.endswith(_suffix_for(backend))
    content = read_log_text(path)
    assert msg in content

    if collapsed:
        assert TIME_IN_NAME.search(path.name) is None
    else:
        assert TIME_IN_NAME.search(path.name) is not None


def test_compression_level_accepted(tmp_path):
    tl = make_tee_logger(
        tmp_path,
        programName="cmp_level",
        in_place_compression="gzip",
        compression_level=3,
    )
    tl.info("leveled")
    close_tee_logger(tl)
    content = read_log_text(find_log_files(tmp_path / "cmp_level_log")[-1])
    assert "leveled" in content


@pytest.mark.zstd
def test_zstd_marker_path(tmp_path):
    pytest.importorskip("compression.zstd")
    tl = make_tee_logger(tmp_path, programName="cmp_zstd_mark", in_place_compression="zstd")
    tl.info("zstd-msg")
    close_tee_logger(tl)
    files = find_log_files(tmp_path / "cmp_zstd_mark_log")
    assert files[-1].name.endswith(".zst")
    assert "zstd-msg" in read_log_text(files[-1])
