import bz2
import gzip
import lzma
import uuid
from pathlib import Path

from Tee_Logger import teeLogger


def unique_name(prefix: str = "tl") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def make_tee_logger(tmp_path, program_name=None, **kwargs):
    """Build a teeLogger rooted at tmp_path with safe defaults for tests."""
    kwargs.setdefault("suppressPrintout", True)
    kwargs.setdefault("disable_colors", True)
    kwargs.setdefault("compressLogAfterMonths", 0)
    kwargs.setdefault("deleteLogAfterYears", 0)
    name = program_name or unique_name()
    kwargs.setdefault("programName", name)
    kwargs.setdefault("systemLogFileDir", str(tmp_path))
    return teeLogger(**kwargs)


def close_tee_logger(tl):
    if getattr(tl, "logger", None) is None:
        return
    for handler in list(tl.logger.handlers):
        tl.logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def find_log_files(log_root: Path):
    files = []
    if not Path(log_root).exists():
        return files
    for path in Path(log_root).rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if "_latest.log" in path.name:
            continue
        if path.suffix in {".log", ".gz", ".bz2", ".xz", ".zst"} or ".log." in path.name:
            files.append(path)
    return sorted(files)


def read_log_text(path: Path) -> str:
    path = Path(path)
    name = path.name
    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if name.endswith(".bz2"):
        with bz2.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if name.endswith(".xz"):
        with lzma.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if name.endswith(".zst"):
        from compression import zstd
        with zstd.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return path.read_text(encoding="utf-8", errors="replace")


def _new_decompressor(name: str):
    if name.endswith(".gz"):
        import zlib
        return zlib.decompressobj(wbits=31)
    if name.endswith(".bz2"):
        return bz2.BZ2Decompressor()
    if name.endswith(".xz"):
        return lzma.LZMADecompressor()
    if name.endswith(".zst"):
        from compression import zstd
        return zstd.ZstdDecompressor()
    return None


def salvage_log_text(path: Path) -> str:
    """Decompress everything readable from a log a crashed process left behind.

    Unlike read_log_text, a missing end-of-stream marker is not an error: we keep
    every byte the decompressor can produce, across concatenated streams.
    """
    path = Path(path)
    data = path.read_bytes()
    out = b""
    if _new_decompressor(path.name) is None:
        return data.decode("utf-8", errors="replace")
    while data:
        d = _new_decompressor(path.name)
        out += d.decompress(data)
        if not d.eof:
            break
        data = d.unused_data
    return out.decode("utf-8", errors="replace")
