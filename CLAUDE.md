# fun_time — Project-Specific Instructions

Shared rules are in the global `~/.claude/CLAUDE.md`. This file contains only fun_time-specific overrides.

**Keep this file short.** No redundancy with the global CLAUDE.md. One bullet per rule. If editing this file, remove or consolidate — never just append.

## Test commands

Unit tests (run freely, no permission needed):
```powershell
.\.venv\Scripts\python.exe -m pytest
```

The default `pytest` invocation only runs unit tests. Integration tests live in `tests/integration/` and are excluded from the default collection.

Integration tests — run on a hidden Win32 desktop so the real windows never touch your screen; safe to run unattended, like unit tests, **including while Fun Time is open**:
```powershell
.\.venv\Scripts\python.exe -m tests.integration.hidden_desktop
```

The runner creates the hidden desktop, sets `FUN_TIME_RUN_INTEGRATION=1`, and runs the whole suite invisibly (real HWNDs, off-screen, never foreground). The machine-wide lock in the integration conftest auto-serializes concurrent agent runs — a second run queues instead of clobbering, so you don't hunt for a quiet window. Extra pytest args pass through (`... hidden_desktop -k nau`).

A run reaches nothing of the user's, so it never has to wait for one. Two mechanisms, and every shared resource belongs to one of them: the hidden desktop covers everything with a per-desktop version (windows, focus, input hooks, AHK's single-instance search, the leftover-process reap), and `integration_support.isolate_shared_resources` strips out everything without one (the three UDP ports, the loopback port, the broker's tray launcher, the microphone) — see its docstring for what each collision was. Adding a new machine-global resource means adding it there; `test_integration_support.py` sweeps a run's config for any surviving mention of the machine's endpoints. What is still shared is the GPU, so a run and a live session compete for decode.

Run the suite only through `hidden_desktop`: `pytest tests/integration/` refuses at session start (exit `4`), because that form puts real windows, a real AHK bridge and real players on your own desktop.

**Green means every collected test passes — zero failures, skips, or deselects.**

Convenience wrapper (unit tests only): `bash test.sh`
If `bash test.sh` fails because Git Bash cannot create its signal pipe, use the direct `.venv` command above.

## Win32 API changes: mandatory pre-flight

Before modifying any Win32 API call (ctypes, keyboard/mouse input, window management, thread input):

1. **State the mechanism.** Explain WHY the approach works, citing the specific Win32 behavior it depends on.
2. **Verify the claim.** If not certain, say so explicitly rather than guessing.
3. **Check interactions.** Identify what other components touch the same subsystem (AHK hooks, the Qt event loop behind the dashboard and overlays, the players' own windows, thread input queues) and explain why the change won't break them.
4. **Map from symptoms.** Trace the execution path that produces the bug and confirm the fix addresses that specific path.

If you cannot complete these steps, stop and say so. Do not submit a speculative fix.

## AHK bridge constraints

`windows_bridge_hotkeys.ahk` runs under `#SingleInstance Force`. Startup checks, integration runs, and AHK launch validations must be executed sequentially — parallel launches can evict each other.

## Integration test fidelity

- **Test configuration must derive from production code.** Integration tests may test individual components (not only end-to-end), but their configuration (launch commands, flags, init sequences) must come from the same production functions that real sessions use. Never hand-craft config that duplicates production logic — if the test builds its own satellite command line instead of calling `_build_satellite_launch_command`, it can pass while production is broken.
- **Integration tests must randomize video selection.** Use `random.sample()` or `random.choice()` — never `sorted()[:n]` or other deterministic selection. The same videos playing every run masks bugs that only surface with different media files.

## The shared repos

- No app may reach into another's repo. What we share lives in siblings installed editable into this venv, and a change to any of it belongs there: `../player_core` (the satellite players' engine, playlist format, command/paused file channel, status writer), `../app_support` (logging setup and exception hooks, `start_daemon_thread`, `preparse_config_path`, `hidden_subprocess_kwargs`), `../shared_ui` (Qt widgets).
- Install each with `--config-settings editable_mode=compat`; their READMEs say why, and each carries a `tests/test_install.py` that goes red without it.
- `satellite/` is a second top-level package in this repo, launched as `python -m satellite` with **our** python (`paths.python_exe`), not genau's. It resolves through the working directory `launch.vbs` sets, the same way `-m fun_time.dashboard_app` does.

## Repo-specific gotchas

- Broker startup flows through `launch_broker_tray.vbs`, not directly to `scripts/run_broker_service.ps1`.
- Random Favs Browser tab opening is sensitive to window focus — preserve explicit Chrome window targeting.
- The test environment is the project `.venv`, not system Python or Conda.

## Test fixtures must be fabricated, never copied from the real library

Every fixture value that stands in for library data — a video title, a filename,
a performer or studio name, prompt text — must be **invented**. Never paste a
real one out of the media library to make a test feel realistic.

This is not a style note. It is the single thing that has actually leaked private
data into these repos: an agent writing a test reached for a real filename or
performer name because it was handy, and it rode into a public commit. Nothing in
the app's *design* pulls library text into source — the library lives outside
every repo, read at runtime through the git-ignored overlays — so this habit is
the only remaining path for a real name to get committed, and the only thing
stopping it is you following this rule.

Do not lean on the sanitize guard to catch it. `tools/sanitize_guard.py` fails
the suite when a **known** blocked term appears in the tracked tree, but a brand-
new performer name it has never seen passes every check and lands. The guard is a
backstop for names already known; it cannot see the next one.

So fabricate fully. Use `Jane Doe`, `Example Studio`, `scene one`, the
`alpha`/`beta`/`gamma` act placeholders the committed `content.example.json`
already uses. The near miss that still counts: taking a real filename and
changing a character or two — it is still that clip, still that performer. Make
it up from scratch, don't lightly edit a real one.

## Landing — GitHub merge queue, not local ff-merge

This repo is public at `github.com/haglio/fun_time` with a merge-queue ruleset on
`main`, so the global "ff-merge into the primary checkout under
`.git/agent-merge.lock`" flow does NOT apply here:

- **Land through a pull request.** From your worktree: commit, `git fetch origin
  && git rebase origin/main`, `git push -u origin <branch>`, then
  `gh pr create --fill`. Auto-merge arms itself; the queue rebases your PR onto
  `main`, runs the required check, and merges it when green. Don't ff-merge into
  the primary checkout, don't push `main` directly, and never force-push `main`.
- **Delete the branch the moment it merges** — `git push origin --delete
  <branch>`, before you tell him the work is done. Neither the queue nor the
  ruleset prunes it, so every landed PR otherwise leaves a branch on origin for
  good, and the next agent reading `git branch -r` for what is in flight has to
  sift the dead from the live.
- **The `.git/agent-merge.lock` is retired here** — the GitHub queue serializes.
- **Sync local checkouts by pulling.** `main` advances only on origin (via the
  queue), so the primary checkout and worktrees update with
  `git pull --ff-only origin main`; the running app self-updates the same way.
  The primary is only ever fast-forwarded — never reset or merged-into.
- **A red required check** (`.github/workflows/merge-gate.yml`) can't land.

- **Get his eyes on the branch before the PR — `launch_branch.vbs`.** He runs Fun
  Time from the primary checkout, which only moves when `main` does, so a branch
  used to be a change he could not see, run or judge; that is why the rule here
  was once "land it unverified rather than park it". It no longer is. With your
  suites green, tell him to open
  [fun_time](C:/Users/Douglas/workspace/haglio/fun_time) and double-click
  `launch_branch.vbs`, then pick your branch from the list (or type part of its
  name). That runs a real session on your worktree's code — his real library, his
  real monitors, uncommitted edits included. It replaces the live session rather
  than joining it, so he quits with Ctrl+Alt+Q and launches Fun Time normally
  afterwards; `fun_time/branch_session.py` says what it isolates and what it
  shares on purpose. Then on his word: PR → queue → `git -C <primary> pull
  --ff-only origin main`, and tell him it is live and needs a restart. Only he
  may waive the launch — "just land it" is his call to make, never yours.

Everything else in the global CLAUDE.md — work in a worktree, green tests before
you push, clean handoff — still applies.
