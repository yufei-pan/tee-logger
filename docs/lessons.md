# Lessons

What this project's past changes taught us. Read all of this before changing an API, a format or an
agreed behaviour here. For the full story of a line, find the entry with the same date and name in
`lessons-full.md`.

When one has to change, add the full entry to `lessons-full.md` and one line here, newest last, in the
same commit as the change. Keep this file under 20 KB: at the cap, merge lines before adding.

- **2026-10-29 tee-logger: Compressed log handlers**: anything appended to a shared file must be self-contained per write; expose the configuration (a backend name), not the classes behind it.
