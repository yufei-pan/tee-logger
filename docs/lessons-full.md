# Lessons: full entries

Why this project's APIs, formats and agreed behaviours had to change. `lessons.md` has one line per
entry: read that first, and look entries up here by date and name rather than reading this whole. New
entries go last, in the same commit as the change:

```markdown
## YYYY-MM-DD <short name>
- Changed: ...
- Why we had to change: ...
- Original design was wrong because: ...
- Lesson: ...
```

## 2026-10-29 tee-logger: Compressed log handlers
- Changed: teeLogger.GZipFileHandler, BZ2FileHandler, XZFileHandler and ZSTDFileHandler removed; one internal handler writes each flush as a whole compressed member.
- Why we had to change: a stream kept open per file left the day's gzip/zstd log unreadable when a killed run's tail was followed by a restarted or concurrent writer, and a forked child copied the open stream.
- Original design was wrong because: it treated a shared, append-only file as one process's private stream, and exposed one class per format with format-specific constructor arguments (already broken once in 6.41).
- Lesson: anything appended to a shared file must be self-contained per write; expose the configuration (a backend name), not the classes behind it.
