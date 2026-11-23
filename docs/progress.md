# tee-logger: progress

Open work lives here and nowhere else (RULES, "Long work: the progress file"). Edit rows in place, in the commit that changes the work.

State at 2026-11-14: v6.45 is the latest release (master 9705450).

## Goal
- 2026-11-05, for every cas tool: "we improve the cas scripts iteratively. feel free to add scope / tasks if when doing some work, you noticed some other thing that can be improved with cas scripts." (the user; no goal for this repo alone is written in the repo)

## Open work

### tee-logger 6.46 (no written plan; the main session's call from the check below, 2026-11-23)
- **A record lost or written twice when a logging signal handler cuts into `emit()` (Python 3.6 to 3.9).** On those Pythons a signal handler that logs can land after `emit()` has loaded the batch list and before it appends; the handler's flush swaps in a new list, so the record goes to the dead one (about 1 in 2,000 to 7,000 handler calls while the program logs without pause; twice, a bz2 record written twice). No file is damaged; 3.10 and later showed none in about 810,000 handler calls. The fix removes the swapped list (a deque drained one record at a time), so each record is taken once; a cheap, safe fix under cas/CLAUDE.md's Python support rule (the main session's call, not asked), measured on 3.11 and 3.14 before it ships. Also to check: a signal between a short write on a full disk and its cut-back. State: building on feat/646 (2026-11-23). From: the check below (a fix with a reproduction, not asked).

### Standing rows
- **Deferred work** is in `docs/limitations.md` (entries of kind "deferred"); each names when it is revisited.

## Standing decisions
The user's rulings on how the cas repos are worked, one line each; a line changes only on the user's word. The cas monorepo's CLAUDE.md holds the same list, with how to reach the test machines.
- Deferrals (2026-11-05): "please do not ask me about features you think should defer to next version anymore. instead, note them down and after getting the current version done, start the next version right away. only ask me to approve things that are going into limitations that we do not have plans to fix it any time soon."
- The release gate (2026-11-09): "releasing a new version gate is only if it does not make an existing issue worse. even if the fix does not completely resolve the problem, if it makes the result more tolerable, we shall not gate it for release because there are still issues."
- Review before upload (2026-10-18): "commit and push is fine but when packaging, please follow the rule that need a code review before uvpack / upload to pypi".
- A test that passed only on a rerun (2026-11-06, "Green with a warning (Recommended)"): CI stays green with a warning, and "Before any upload, every such test still needs a written cause either way."
- Release pushes (2026-11-10, "Release OK covers it (Recommended)"): an OK for a release covers its push of main, the version tag and the GitHub release.
- Tests (2026-11-12, "Core per release, full at roll-up (Recommended)"): CI's own command on Python 3.14 and 3.11 in Docker before each release; the full gate (every Python in CI, macOS, EL7 on Python 3.11) before a Monday roll-up pin moves.
- CentOS 7 (2026-11-04): "for centos 7, which is already EOL, we only target all the last packages released for it. this also includes EPEL which is also in archive right now."
- EL8 (2026-11-05): "yes rocky 8 it is. let's also only test on them when you think test on real kernel is needed."
- macOS (2026-11-11): tests run on the user's own always-on Mac, as a standard user made for them, never with sudo.

## Questions waiting for the user
- None.

## Closed
- The 6.43 review's two unchecked design items (2026-11-23, feat/646 9fd3707, two regression tests): neither happens on 6.45. (2) A logging signal handler during an xz or bz2 finish writes its own whole member: ended in 6.44 by fe26032 (a member is built whole, then one write); 0 damaged files in 7,490 (about 1.4M handler calls, 740,373 inside a flush) on Python 3.6 to 3.14, where the commit before fe26032 damaged 60 of 60. (3) A record after the midnight switch goes to the new day's file and leaves no flusher: ended by 8d8200c (the switch keeps the handler and swaps its file) and fe26032 (a closed handler stops its flusher).
