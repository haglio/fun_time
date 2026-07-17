# Genau Auto-Mode Startup — Attempt 2 Report

**Date:** 2026-03-27
**Agent:** Claude Opus 4.6 (1M context)
**Outcome:** Failed. All changes reverted in commit `9f514e4`.

---

## Goal

When the OSR2 is already in auto mode when Fun Time loads, Genau
should begin active instead of Primary VLC.

---

## What Was Tried

### Pass 1: Patch the existing file-based architecture (failed)

Four changes were made to the existing file-polling design:

1. **Shutdown persistence** — Write `genau_mode_at_shutdown.txt` on
   exit, read on next startup to initialize `genau_mode=True`.
2. **Startup mode file override** — After the broker resets
   `genau_mode.txt` to "0", overwrite it with "1" from the
   orchestrator so the dispatch loop stays consistent.
3. **BPM/stroke auto-mode inference** — The OSR2 doesn't re-announce
   "Auto mode is on!" if it was already on before MFP started. But it
   does send BPM and stroke pattern messages (exclusive to auto mode).
   Added inference in `handle_line` to detect these and call
   `set_auto(True)`.
4. **Initial dispatch loop state** — Pass `initial_genau_mode=True`
   to `DispatchLoopRunner` so `BridgeState` starts correctly.

**Why it failed:** Multiple cascading issues:
- The dispatch loop's `sync_genau` calls `ensure_playback_state`
  every 200ms, which immediately undid the startup pause.
- The Genau window show/hide was tied to transitions
  (`is_transition=True`), but starting in Genau mode isn't a
  transition — the window was never shown.
- Added retry logic for the window show, but `find_window_by_title`
  requires `IsWindowVisible`, and the Genau app starts its window
  hidden (waiting for UDP SHOW from the broker).
- Even after all fixes, manual testing showed no activation. The broker
  DID detect auto mode (confirmed in logs: `AUTO ON` within 4 seconds
  of startup), but the dispatch loop's file read and the Genau
  app's own visibility management were fighting each other.

### Pass 2: Rewrite to UDP-based architecture (failed harder)

Designed and implemented a clean rewrite with 7 commits:

1. **`dispatch_udp.py`** — New UDP listener for AUTO messages
2. **Broker multi-target UDP** — Send AUTO to both Genau app and
   dispatch loop
3. **Wire dispatch loop to UDP** — `apply_sync_genau` takes
   `mode_state_on` as parameter instead of reading file
4. **Remove dispatch-side Genau show/hide** — Let Genau app
   be sole visibility controller
5. **Fix stale timeout** — Use BPM/stroke evidence time
6. **`ensure_playback_state` only on transitions** — Stop 200ms polling
7. **Orchestrator wiring** — UDP receiver, shutdown persistence, startup

**Why it failed:** The `AutoModeReceiver` binds to port 50556 on
localhost. On the user's Windows 11 machine, this threw:
```
PermissionError: [WinError 10013] An attempt was made to access a
socket in a way forbidden by its access permissions
```
This crashed the orchestrator BEFORE the AHK hotkey script launched,
which meant:
- Fun Time appeared to start (VLCs and MFP launched during Phase 1)
  but the dispatch loop and hotkey script never ran
- Ctrl+Opt+Q didn't work (AHK never started)
- The Dashboard was orphaned (launched in Phase 3, never cleaned up)
- Genau mode obviously never activated

The integration tests didn't catch this because they test within the
Python process (mocking out subprocesses), not the full system. The
UDP bind failure only manifests on the real Windows runtime.

---

## Key Technical Findings

### Hardware constraint (confirmed)
The OSR2 only sends "Auto mode is on!" when it **transitions** to auto
mode while receiving T-code. If auto mode was already on before MFP
started, no transition message is sent. **However**, BPM and stroke
pattern messages ARE sent during auto mode and are exclusive to it.
This inference works — broker logs confirm `AUTO ON` within 4 seconds
of startup.

### The stale timeout kills legitimate auto mode
When auto mode is active, T-code from MFP is blocked (by design).
The OSR2's auto patterns eventually stop sending data. After
`auto_stale_timeout` seconds of silence, the broker kills auto mode.
In logs, auto mode lasted ~50 seconds before stale timeout fired.
BPM/stroke evidence time can prevent this.

### Dual visibility controllers race
The dispatch loop uses win32 `find_window_by_title("Genau")` +
`show_window`/`hide_window`. The Genau app uses tkinter
`deiconify`/`withdraw` based on UDP SHOW/HIDE from broker. These
operate on different schedules using different mechanisms and override
each other. The Genau app starts its window hidden and only shows
it when it receives UDP SHOW — so `find_window_by_title` (which
requires `IsWindowVisible`) can't find it.

### `ensure_playback_state` called every 200ms
The `apply_sync_genau` function is called every 200ms and always
calls `ensure_playback_state`, even when there's no state change.
This means any transient VLC state (like pausing Primary VLC at
startup) gets immediately undone by the next sync tick.

### Windows port binding issues
Port 50556 (and possibly other ports in the 50000+ range) may be
blocked by Windows Hyper-V, Docker, or firewall rules. Any UDP
listener approach must handle this gracefully — either by using
ephemeral ports with a discovery mechanism, or by falling back to
another IPC method.

---

## Architecture Assessment

The current architecture has these fundamental problems:

1. **`genau_mode.txt` has 3 writers** (broker, orchestrator, stale
   timeout) and 1 reader (dispatch loop)
2. **Two visibility controllers** for the same window
3. **Continuous state enforcement** (200ms polling) that fights transient
   overrides
4. **Stale timeout** that kills legitimate auto mode

A clean solution needs:
- **Single source of truth** for auto mode state
- **Single visibility controller** per window
- **Transition-only side effects** (not continuous enforcement)
- **Reliable IPC** that works on Windows without port permission issues

### Approaches NOT to try
- **Adding more file-based coordination** — the multi-writer race is
  fundamental
- **UDP on fixed ports** — Windows port permissions are unpredictable
- **Patching the timing** — there are too many interacting timing
  dependencies

### Approaches worth considering
- **Named pipes** (Windows native IPC, no port issues)
- **Shared memory** via `multiprocessing.shared_memory`
- **Ephemeral UDP with port discovery** via a file (port 0 bind, write
  actual port to a file that others read)
- **Simplify to single-process** — move the relevant broker logic into
  the orchestrator process so there's no IPC needed for mode state

---

## State of the Codebase

All changes from both passes have been reverted. The codebase is clean
at commit `9f514e4` (revert commit) which is functionally identical to
`562e007` (the pre-attempt state).

The BPM/stroke inference code is **not** in the codebase (reverted) but
is preserved in git history on the `robot-hand-mode-rewrite` branch
commits. The inference logic itself is correct and should be reused.

---

## Files to Understand

| File | Role |
|------|------|
| `broker_protocol.py` | `BrokerAutoController` — auto mode detection, UDP broadcast |
| `broker_session.py` | Serial forwarding, T-code blocking, stale timeout |
| `broker_app.py` | Broker process entry point |
| `command_dispatch.py` | `_dispatch_sync_genau` — reads mode file, manages state |
| `runtime_flow.py` | `apply_sync_genau` — reads files, calls VLC, writes paused files |
| `genau_plan.py` | Pure state machine for transition decisions (well-tested, correct) |
| `windows_bridge_dispatch_loop.py` | `DispatchLoopRunner` — polls mode file every 200ms |
| `windows_bridge_orchestrator.py` | `should_start_in_genau_mode`, startup flow |
| `genau/app.py` | Genau clip player subprocess |
| `genau/state.py` | UDP listener in Genau app |
| `genau/notifier.py` | `sync_window_visibility` — Genau's own show/hide |
