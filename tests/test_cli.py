import subprocess
import sys
from pathlib import Path

import pytest

import Tee_Logger

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "Tee_Logger.py"


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_version_flag(flag):
    proc = subprocess.run(
        [sys.executable, str(MODULE), flag],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    out = (proc.stdout or "") + (proc.stderr or "")
    assert Tee_Logger.version in out
