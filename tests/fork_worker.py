"""Worker targets for the multiprocessing tests, in a module the worker imports itself."""
from Tee_Logger import teeLogger


def log_once(root, msg):
    tl = teeLogger(systemLogFileDir=root, programName="mp_worker", in_place_compression="gzip",
                   suppressPrintout=True, compressLogAfterMonths=0, deleteLogAfterYears=0, flush_interval=60)
    tl.info(msg)
