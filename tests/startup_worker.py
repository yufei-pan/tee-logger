"""A module whose logger is made, and logs, when a worker imports it to unpickle its target."""
import os

from Tee_Logger import teeLogger

if os.environ.get("TL_STARTUP_ROOT"):  # not when pytest imports it for doctests
    tl = teeLogger(systemLogFileDir=os.environ["TL_STARTUP_ROOT"], programName="startup_worker",
                   in_place_compression="gzip", suppressPrintout=True, compressLogAfterMonths=0,
                   deleteLogAfterYears=0, flush_interval=60)
    tl.info(f"imported in {os.getpid()}")


def nothing():
    pass  # logs nothing more once the worker runs
