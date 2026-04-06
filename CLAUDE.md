# fun_time — Project-Specific Instructions

Shared rules are in the global `~/.claude/CLAUDE.md`. This file contains only fun_time-specific overrides.

**Keep this file short.** No redundancy with the global CLAUDE.md. One bullet per rule. If editing this file, remove or consolidate — never just append.

## Test commands

Unit tests (run freely, no permission needed):
```powershell
.\.venv\Scripts\python.exe -m pytest
```

The default `pytest` invocation only runs unit tests. Integration tests live in `tests/integration/` and are excluded from the default collection.

Integration tests (always ask permission first):
```powershell
FUN_TIME_RUN_INTEGRATION=1 .\.venv\Scripts\python.exe -m pytest tests/integration/
```

**You must run ALL integration test files** — the command above runs everything in `tests/integration/`. `FUN_TIME_RUN_INTEGRATION=1` is required — without it, tests skip silently.

**The suite is only green when every test passes with zero skips and zero deselected.** Skipped tests are NOT passing tests. Deselected tests are NOT passing tests. If the output shows any skips or deselections, stop and fix the invocation before committing.

Convenience wrapper (unit tests only): `bash test.sh`
If `bash test.sh` fails because Git Bash cannot create its signal pipe, use the direct `.venv` command above.

## Win32 API changes: mandatory pre-flight

Before modifying any Win32 API call (ctypes, keyboard/mouse input, window management, thread input):

1. **State the mechanism.** Explain WHY the approach works, citing the specific Win32 behavior it depends on.
2. **Verify the claim.** If not certain, say so explicitly rather than guessing.
3. **Check interactions.** Identify what other components touch the same subsystem (AHK hooks, VLC's Qt event loop, thread input queues) and explain why the change won't break them.
4. **Map from symptoms.** Trace the execution path that produces the bug and confirm the fix addresses that specific path.

If you cannot complete these steps, stop and say so. Do not submit a speculative fix.

## AHK bridge constraints

`windows_bridge_hotkeys.ahk` runs under `#SingleInstance Force`. Startup checks, integration runs, and AHK launch validations must be executed sequentially — parallel launches can evict each other.

## Integration test fidelity

- **Test configuration must derive from production code.** Integration tests may test individual components (not only end-to-end), but their configuration (launch commands, flags, init sequences) must come from the same production functions that real sessions use. Never hand-craft config that duplicates production logic — if the test builds its own VLC command line instead of calling `_build_vlc_launch_command`, it can pass while production is broken.
- **Integration tests must randomize video selection.** Use `random.sample()` or `random.choice()` — never `sorted()[:n]` or other deterministic selection. The same videos playing every run masks bugs that only surface with different media files.

## Repo-specific gotchas

- Broker startup flows through `launch_broker_tray.vbs`, not directly to `scripts/run_broker_service.ps1`.
- Random Favs Browser tab opening is sensitive to window focus — preserve explicit Chrome window targeting.
- The test environment is the project `.venv`, not system Python or Conda.
