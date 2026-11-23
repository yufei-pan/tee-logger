# tee-logger: progress

Open work lives here and nowhere else (RULES, "Long work: the progress file"). Edit rows in place, in the commit that changes the work.

State at 2026-11-14: v6.45 is the latest release (master 9705450).

## Goal
- 2026-11-05, for every cas tool: "we improve the cas scripts iteratively. feel free to add scope / tasks if when doing some work, you noticed some other thing that can be improved with cas scripts." (the user; no goal for this repo alone is written in the repo)

## Open work

### The 6.43 review's open design items (no written plan; the simplify pass's notes listed them, 2026-10-26)
- **Check whether two of them still happen on 6.45.** The simplify pass's notes listed six design items from the 6.43 review and asked that each one still standing go to the user (fix it, or a known limitation). README "Upgrading to 6.44" shows four were settled: whole-member compressed writes with a failed write cut back (item 1), a user's own `FileHandler` left alone (item 4), the midnight-switch deadlock (item 5), and the flusher's start and the console's `print()` (most of item 6). Not checked against the code: (2) a signal handler logging WARNING or above during an xz or bz2 finish writes a second stream inside the first; (3) a record sent to an old compressed handler after the midnight switch closed it reopens yesterday's file, with a flusher nothing stops. State: checking (2026-11-23): reproductions on 6.45, failing tests on feat/646 for any that still happens. Where: reproduce each on 6.45 with a real signal and a real midnight switch (a fake clock); for one that still happens, a fix or a known-limitation question for the user. From: the 6.43 review (the main session's plan); the 2026-11-14 scope audit found no recorded outcome.

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
- (none yet)
