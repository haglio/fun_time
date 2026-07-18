# fun_time — Project-Specific Instructions

Shared rules are in the global `~/.claude/CLAUDE.md`. This file contains only fun_time-specific overrides.

**Keep this file short.** No redundancy with the global CLAUDE.md. One bullet per rule. If editing this file, remove or consolidate — never just append.

## Test commands

Unit tests (run freely, no permission needed):
```powershell
.\.venv\Scripts\python.exe -m pytest
```

The default `pytest` invocation only runs unit tests. Integration tests live in `tests/integration/` and are excluded from the default collection.

Integration tests — run on a hidden Win32 desktop so the real windows never touch your screen; safe to run unattended, like unit tests:
```powershell
.\.venv\Scripts\python.exe -m tests.integration.hidden_desktop
```

The runner creates the hidden desktop, sets `FUN_TIME_RUN_INTEGRATION=1`, and runs the whole suite invisibly (real HWNDs, off-screen, never foreground). The machine-wide lock in the integration conftest auto-serializes concurrent agent runs — a second run queues instead of clobbering, so you don't hunt for a quiet window. Extra pytest args pass through (`... hidden_desktop -k nau`).

A run brings up a second session, competing for the GPU and driving the shared OSR2 broker, so it can never share the machine with a live Fun Time. A running session publishes a claim to a fixed machine-global path (`%LOCALAPPDATA%\FunTime\live_session.ini`) — *not* to its state dir, which every worktree resolves to itself — and `live_session_guard` reads that. Before the desktop exists: no session → run; session playing or still starting → **denied** (exit `4`); session in OmniPause → the user gets a prompt to close it or refuse the run. It keeps watching for the whole run too, and aborts (exit `5`) if Fun Time opens mid-run. Exits `4` and `5` are refusals, not failures — do not retry them.

Run the suite only through `hidden_desktop`: `pytest tests/integration/` refuses at session start, because that form puts real windows, a real AHK bridge and real players on your own desktop.

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

## The shared player core

- The satellite players' playback engine, the playlist format, the command/paused file channel and the status writer live in `../player_core`, installed editable into this venv — `../genau`'s apps use them too, and no app may reach into another's repo. Fix those there, not here.
- Install it with `--config-settings editable_mode=compat`; `player_core`'s README says why, and its `tests/test_install.py` goes red without it.
- `satellite/` is a second top-level package in this repo, launched as `python -m satellite` with **our** python (`paths.python_exe`), not genau's. It resolves through the working directory `launch.vbs` sets, the same way `-m fun_time.dashboard_app` does.

## Repo-specific gotchas

- Broker startup flows through `launch_broker_tray.vbs`, not directly to `scripts/run_broker_service.ps1`.
- Random Favs Browser tab opening is sensitive to window focus — preserve explicit Chrome window targeting.
- The test environment is the project `.venv`, not system Python or Conda.
