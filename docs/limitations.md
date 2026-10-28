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
- What the operator sees: a forked child that logs through the compressed logger it inherited loses the
  records it logged since its last write (up to `flush_interval` seconds of records below WARNING) when it
  leaves with `os._exit` (a child of a bare `os.fork()`) or is killed. That includes the workers of a
  `fork` Pool ended by `terminate()`, which is what `with Pool() as p:` does on exit: their unwritten INFO
  records are gone. multiprocessing workers that end normally (a `Process`, or a Pool closed with `close()`
  and `join()`) write them through an exit finalizer, and `forkserver` and `spawn` workers (3.14's default is
  `forkserver`) kept them in every case measured. 6.43 lost them too, and its fork children corrupted gzip
  and zstd logs.
- Why: `os._exit` skips every exit hook, and `terminate()` sends SIGTERM. The only cure, writing every
  record of a worker at once, costs worker records 3 times the size with gzip or zstd, and about 1 ms of CPU
  and 8 - 11 times the size per record with xz or bz2.
- Revisit when: a program needs the last records of its fork Pool workers or hand-written fork children.
- Approved: 2026-10-28, the user chose "Flush at worker exit (Recommended)", then, told about `terminate()`,
  "Keep the exit flush (Recommended)".
