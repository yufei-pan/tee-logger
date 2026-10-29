"""A worker target that imports Tee_Logger only once the worker runs it."""


def log_once(root, msg):
    from Tee_Logger import teeLogger

    tl = teeLogger(systemLogFileDir=root, programName="lazy_worker", in_place_compression="gzip",
                   suppressPrintout=True, compressLogAfterMonths=0, deleteLogAfterYears=0, flush_interval=60)
    tl.info(msg)
