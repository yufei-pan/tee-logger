# Limitations

Approved known limitations and deferrals. Reviewers: read this first, and don't raise these again.

### Frequent flushes make gzip and zstd logs larger
- Kind: known limitation
- What the operator sees: each flush of a compressed log is compressed on its own, so gzip and zstd no longer
  use what earlier batches held. Measured on 60,000 log lines: no change for a log of INFO records, +2 - 5%
  when one record in 100 is WARNING or higher (each of which flushes), +40 - 45% when one in 10 is, and three
  times the size with `flush_interval=0`. xz and bz2 already finished a stream at every flush in 6.43 and are
  unchanged. Records still waiting when the process forks are written just before the fork, so a program
  that forks often while records wait gets one more member per fork: a gzip log 8.6 times the size for a
  loop that forks once per request and logs one INFO line each (2,000 forks).
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

### The stderr fallback can split a line on Python 3.6 and 3.7
- Kind: known limitation
- What the operator sees: only when neither the log directory nor the private fallback directory can be
  written, so records go to stderr, and only on Python 3.6 and 3.7: the stdlib handler writes a record and
  its newline separately, so another thread's stderr output can land between them. 6.43 wrote both at once.
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
  parent and starts a flusher of its own, a multiprocessing worker writes its last records, and a child
  forked during another thread's midnight switch gets a rollover lock of its own. But nothing is written
  before the fork, so a daemon whose parent leaves with `os._exit` loses the records still waiting then (its
  startup lines). And, as with any logging handler on 3.6, a child forked while another thread holds the
  handler's lock (writing a record or a batch, or switching to the new day's file) hangs at its first
  record: 3.6 does not re-create handler locks in a child. With xz, a 30 s interval and a fork now and then,
  that is about one fork in 15,000. 3.7+ is not affected.
- Why: 3.6 is past its end of life; the pid check covers batches, the flusher and the rollover lock at one
  `os.getpid()` per record, and nothing in a handler can take back a lock a thread of the parent held.
- Revisit when: 3.6 support ends, and the pid check can go.
- Approved: 2026-10-28, the user chose "Add the 3.6 pid check", whose question named the daemon's lines;
  the handler lock: 2026-10-29, the user chose "Known limitation (Recommended)".

### A Ctrl-C while the main thread writes a compressed batch loses that batch
- Kind: deferred (6.47)
- What the operator sees: a compressed log whose main thread is inside a flush, after it took the waiting records and
  before the write has returned (compressing or writing them), when a SIGINT arrives, or a SIGTERM whose handler calls
  `sys.exit()`. The exception leaves `flush()` (it catches only `Exception`), the records it took exist only in its
  local list, and the flush at exit finds none: the batch is missing from the log, up to `flush_interval` seconds of
  records or 1 MiB, with the WARNING that started the flush. Shown with a real SIGINT sent from inside the compress
  call (gzip), 6.45 and 6.46 on Python 3.6.15, 3.11.18 and 3.14.8: `KeyboardInterrupt`, 0 of 6 records in the file
  after `logging.shutdown()`. Only a flush on the main thread is exposed (the flusher thread gets no signals), for as
  long as a flush takes: by the 6.46 builder's flush times (not measured as an exposure) up to 24 ms with xz and 3.8 ms
  with gzip for a full batch, so a daemon at the default 30 s is exposed well under 0.1% of the time, and a program
  that logs flat out through xz about a quarter of it. 6.44 and 6.45 the same.
- Why: found by the 6.46 final review; the same in 6.45, so not a hold for 6.46. It is one C call to fix since 6.46
  made the batch a deque.
- Plan (6.47): in `flush()`, on a `BaseException` raised from the drain to the return of `write()`, put the taken
  records back at the front of the batch (`self._batch.extendleft(reversed(batch))`) and re-raise; the flush at exit
  then writes them. A signal that lands just as `write()` returns then writes that batch twice at exit instead of
  losing it: the 6.47 build weighs that, and says so in its notes. A test sends a real SIGINT from inside the compress
  call, as the final review's probe (kept outside the repo) did.
- Revisit when: 6.47.
- Noted: 2026-11-23, the 6.46 final review's finding F1; a deferral, not asked.

### Two threads can deadlock at local midnight when one logs from inside another's record
- Kind: deferred (6.47)
- What the operator sees: the process hangs at local midnight, with no more log lines. It needs two threads. B holds
  the log handler's lock and logs through the same `teeLogger` from inside it (a signal handler that logs landing in
  `emit()`, or a `__str__` that logs while its record is formatted); B's midnight check passes because A, the first
  to log after midnight, has not yet recorded the new midnight; and A takes the rollover lock before B does. B then
  waits for the rollover lock, which A holds, and A, which goes on to switch to the new day's file, waits for the
  handler lock, which B holds. Shown with the window forced by hooks (A parked right after it takes the rollover
  lock; a probe kept outside the repo), Python 3.11 and 3.14: both threads stay stuck. The window is narrow and
  opens once a day: the log call from inside the handler lock has to fall between another thread's midnight check
  and its taking of the lock. 6.45 the same (6.46 does not touch `_rollover()`).
- Why: found by the second opinion on the 6.46 final review, read in the code and reproduced as above; not a hold for
  6.46 (the same in 6.45).
- Plan (6.47): in `_rollover()`, take the rollover lock without blocking (`lock.acquire(False)`; the thread that
  holds it does the switch, and a thread that finds it taken goes on logging to the old file and checks again at its
  next record), or claim `_next_rollover` before taking the lock. On a scratch copy with the first form, the forced
  window ends without a hang on 3.11 and 3.14. The lock is one for every `teeLogger` in the process, so with the first
  form a `teeLogger` that finds it taken by another's switch rolls over at its next record. The test forces the
  window the same way.
- Revisit when: 6.47.
- Noted: 2026-11-23, a lead of the second opinion on the 6.46 final review (H1); a deferral, not asked.
