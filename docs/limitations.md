# Limitations

Approved known limitations and deferrals. Reviewers: read this first, and don't raise these again.

### Frequent flushes make gzip and zstd logs larger
- Kind: known limitation
- What the operator sees: each flush of a compressed log is compressed on its own, so gzip and zstd no longer
  use what earlier batches held. Measured on 60,000 log lines: no change for a log of INFO records, +2 - 5%
  when one record in 100 is WARNING or higher (each of which flushes), +40 - 45% when one in 10 is, and three
  times the size with `flush_interval=0`. xz and bz2 already finished a stream at every flush in 6.43 and are
  unchanged. Records still waiting when the process forks are written just before the fork (Python 3.7 and
  later; 3.6 has no at-fork hooks), so a program that forks often while records wait gets one more member
  per fork: a gzip log 8.6 times the size for a loop that forks once per request and logs one INFO line
  each (2,000 forks).
- Why: a whole gzip member (xz or bz2 stream, zstd frame) per flush is what keeps the file readable when a
  run is killed and the next one appends to the same day's file, when two processes write it, and after a
  short write on a full disk. A stream kept open across flushes cannot give that: 6.43's left the rest of the
  day unreadable in all three cases. The write before a fork keeps a daemon's first lines: its parent
  leaves with `os._exit`, and after the fork the records would otherwise be left to it, or written twice.
- Revisit when: a program that warns often, or forks often, needs smaller gzip or zstd logs.
- Approved: 2026-10-24, the user chose "Whole members (Recommended)". The fork part: 2026-10-28, the user
  chose "Add it to the entry (Recommended)".

### A compressed batch that cannot be written is lost
- Kind: known limitation
- What the operator sees: when the write of a compressed batch fails (a full disk), its records are
  dropped: up to `flush_interval` seconds of records below WARNING, or a WARNING and the records before it.
  Each failed write prints a logging traceback on stderr, at exit too. A partial write is cut back, so the
  file stays readable, and once space is freed later records are written as usual. 6.43 kept gzip and zstd
  data in the open stream, which then often could not be read past that point; it dropped xz and bz2
  batches as now, and said nothing when that happened at exit.
- Why: keeping failed batches takes memory without bound while the disk stays full, plus retry logic; an
  uncompressed log loses the same records.
- Revisit when: someone needs records kept through a full disk.
- Approved: 2026-10-24, the user chose "Whole members (Recommended)", whose question named the dropped
  batch, and "One per failure (Recommended)" for the reports.

### binary_mode=False does not apply to compressed logs
- Kind: known limitation
- What the operator sees: a compressed log is always written as encoded bytes with `\n` line ends, and a
  character the encoding cannot hold is escaped (`namereplace`). With `binary_mode=False`, 6.43 wrote it
  through a text layer instead: `\r\n` line ends on Windows, and a record with such a character was dropped
  with a logging error.
- Why: batches are compressed as bytes; a text layer per batch is code for no gain a reader would notice.
- Revisit when: someone needs `\r\n` line ends inside compressed logs.
- Approved: 2026-10-24, the user chose "Whole members (Recommended)", whose question named this.

### A flusher thread that cannot be started is not started again
- Kind: known limitation
- What the operator sees: when a compressed logger cannot start its background flusher (the process is at
  its thread limit, "can't start new thread"), one logging traceback reports it and no other start is tried.
  Records then reach the disk on the next WARNING or higher, on the next record once `flush_interval` has
  passed, and at exit, but not on a timer: if the process is then killed, the records logged since the last
  write are lost. 6.43 tried again once per interval.
- Why: the retry took about 30 lines and its tests, for a process that is already out of threads.
- Revisit when: someone hits it.
- Approved: 2026-10-24, the user chose "Known limitation (Recommended)".

### The stderr fallback can split a line on Python 3.6 and 3.7.0 to 3.7.1
- Kind: known limitation
- What the operator sees: only when neither the log directory nor the private fallback directory can be
  written, so records go to stderr, and only on Python 3.6 and 3.7.0 to 3.7.1: the stdlib handler writes a
  record and its newline separately (3.7.2 and later write both at once), so another thread's stderr output
  can land between them. 6.43 wrote both at once.
- Why: a 15-line handler subclass, for two end-of-life Pythons, in a fallback behind two failures.
- Revisit when: someone hits it.
- Approved: 2026-10-24, the user chose "Drop it (Recommended)".

### A day-folder named after no real date is never cleaned up
- Kind: known limitation
- What the operator sees: a folder under `{programName}_log/` whose name looks like a date but is not one
  (`2020-13-45`) is never archived or deleted, and each start and each midnight print
  `[ERROR] Failed to get the age of 2020-13-45 (...), skipping` on stderr. 6.43 aged such a folder by its
  mtime or ctime. Neither teeLogger nor our tools make such names.
- Why: the fallback was a second way to age a folder, for names only a person or another tool could make.
- Revisit when: something makes such folders.
- Approved: 2026-10-28, the user chose "Skip it (Recommended)".

### A forked child that leaves with os._exit or is killed loses its last records
- Kind: known limitation
- What the operator sees: a forked child that logs through a compressed logger loses the records it logged
  since its last write (up to `flush_interval` seconds of records below WARNING) when it leaves with
  `os._exit` (a child of a bare `os.fork()`) or is killed. That includes the workers a Pool's `terminate()`
  kills, which is what `with Pool() as p:` does on exit: those that have not ended yet lose their unwritten
  records, whatever the start method. multiprocessing workers that end normally (a `Process`, or a Pool
  closed with `close()` and `join()`) write them through an exit finalizer, with `fork`, `forkserver` and
  `spawn` alike. On Python 3.12 and older such a worker runs its exit finalizers before it waits for its
  own non-daemon threads, so what those threads log below WARNING after the worker's target has returned is
  lost too. 6.43 lost all of these, and its fork children corrupted gzip and zstd logs.
- Why: `os._exit` skips every exit hook, and `terminate()` sends SIGTERM. The only cure, writing every
  record of a worker at once, costs worker records 3 times the size with gzip or zstd, and about 1 ms of CPU
  and 8 - 11 times the size per record with xz or bz2.
- Revisit when: a program needs the last records of its fork Pool workers or hand-written fork children.
- Approved: 2026-10-28, the user chose "Flush at worker exit (Recommended)", then, told about `terminate()`,
  "Keep the exit flush (Recommended)", and "Add it to the entry (Recommended)" for the worker threads on
  3.12 and older.

### On Python 3.6, records waiting at a fork are not written first, and a child can hang
- Kind: known limitation
- What the operator sees: Python 3.6 has no at-fork hooks, so a compressed handler finds out about a fork
  from its pid, at the child's first record. A child then still leaves the parent's waiting records to the
  parent and starts a flusher of its own, and a multiprocessing worker writes its last records. But nothing
  is written before the fork, so a daemon whose parent leaves with `os._exit` loses the records still waiting
  then (its startup lines). And, as with any logging handler on 3.6, a child forked while another thread holds the
  handler's lock (writing a record or a batch, or switching to the new day's file) hangs at its first
  record: 3.6 does not re-create handler locks in a child. With xz, a 30 s interval and a fork now and then,
  that is about one fork in 15,000. 3.7+ is not affected.
- Why: 3.6 is past its end of life; the pid check covers batches and the flusher at one
  `os.getpid()` per record, and nothing in a handler can take back a lock a thread of the parent held.
- Revisit when: 3.6 support ends, and the pid check can go.
- Approved: 2026-10-28, the user chose "Add the 3.6 pid check", whose question named the daemon's lines;
  the handler lock: 2026-10-29, the user chose "Known limitation (Recommended)".

### A signal handler that logs to a plain log can lose its record while the main thread writes
- Kind: deferred (6.48)
- What the operator sees: a plain (uncompressed) log, a signal handler that logs, and a signal that lands while
  the main thread is inside a write of the log file: the handler's record is not written, and a logging traceback
  (`RuntimeError: reentrant call inside <_io.BufferedWriter>`) goes to stderr. Shown by a stress (a separate
  process sent a signal every 0 to 0.5 ms while 4 threads logged flat out): 22 to 37 handler records lost out of
  1,285 to 3,475 handler calls per run, in 6.46 and 6.47 alike, on Python 3.6, 3.9, 3.11 and 3.14. A real program
  is exposed far less, as its signals are rarer and its writes shorter. Compressed logs are not affected (0 lost in
  the same stress).
- Why: found by the 6.47 final review's stress (2026-11-23); the same in 6.46, so not a hold for 6.47.
- Plan (6.48): the plain handler writes and flushes every record anyway, so open a plain binary log unbuffered
  (`buffering=0` in `_LogFileHandler._open`), as the compressed handler's file is. On a scratch copy with that
  one line the same stress had 0 lost and 0 errors on 3.6, 3.11 and 3.14. The build checks short writes (an
  unbuffered write may return fewer bytes than it was given) and `binary_mode=False`.
- Revisit when: 6.48.
- Noted: 2026-11-23, lead L1 of the 6.47 final review; a deferral, not asked.

### Records that reach the log handler through the stdlib logger never switch to the new day's file
- Kind: deferred (6.48)
- What the operator sees: only teeLogger's own methods check the day (`log_with_caller_info`). A program that
  logs through the stdlib logger named `programName`, or a child logger of it (`programName.worker`), keeps
  writing to the previous day's file after midnight until its first call of a teeLogger method; a process that
  never makes one never switches. Read in the code, not run; the same in 6.46 and earlier.
- Why: found by the second opinion on the 6.47 final review (2026-11-23); not a hold, the same in 6.46.
- Plan (6.48): two routes weighed. (a) Check the day where every record passes, in a filter of the file handler:
  `Handler.handle` runs filters before it takes the handler's lock, so `_rollover()` takes the lock as it does
  now, about 4 lines and a test, and `emit()`, whose order 6.47 settled, is untouched. (b) Say in the README that
  only teeLogger's own calls switch: no code, but the gap stays. Take (a), unless the build finds the filter
  recursing through `_rollover()`'s own `Continued from` record or slowing a record; then (b).
- Revisit when: 6.48.
- Noted: 2026-11-23, lead 1 of the 6.47 final review's docs second opinion; a deferral, not asked.
