# Genau Auto-Mode Startup — Investigation Report

> **Historical.** Written against the VLC/MFP architecture, none of which is
> left: there is no `should_start_in_genau_mode()` in
> `windows_bridge_orchestrator.py` and no `genau_mode_at_shutdown.txt`. The
> "shutdown persistence" idea below did eventually land, but for the mode the
> *user* left the session in, not for the OSR2's auto mode: the mode rides the
> shared state file (`session_resume.RESUMED_FIELDS`) and startup seeds the
> primary slot for it (`windows_bridge_startup.seed_startup_states`). The
> hardware findings are the part still worth reading; the auto-mode trigger
> itself is still unsolved.

**Goal:** When Fun Time starts and the OSR2 is already in auto mode, Genau
should be active from the beginning instead of Primary VLC.

---

## What I Tried (All Failed)

### Attempt 1 — Preserve broker mode file across restart
Modified `broker_app.py` to read the existing mode file at startup instead of
resetting it to "0". Hypothesis: if the file already said "1", the system
would see auto mode as active.

**Why it failed:** The broker's 8-second stale timeout resets the flag to "0"
as soon as serial traffic stops. Even if the file survived startup, it gets
cleared within seconds.

### Attempt 2 — Skip `pl_next` during core session launch when auto mode on
Threaded a `genau_auto_mode` parameter through `start_core_session` and
`run_startup_sequence` to skip the initial `pl_next` call.

**Why it failed:** The mode file is always "0" at the point it was being read
(same stale-timeout problem as above). The skip condition never fired.

### Attempt 3 — Read mode file before killing the old broker
Moved the mode file read to before `restart_broker()` so the old broker's
last-written value could be captured.

**Why it failed:** Still the stale-timeout problem. The old broker had already
reset the file to "0" well before startup.

---

## Key Hardware Discovery

**The OSR2 only sends "Auto mode is on!" over serial when MFP is actively
sending T-code.** Pressing the physical auto button while MFP is idle produces
no detectable serial output — only the background temperature reports every ~5s.

This was confirmed by adding `INFO`-level serial logging to `broker_protocol.py`
and reading `state/broker.log`. Prior to MFP starting (or between sessions),
the serial line is silent on the auto-mode front.

This invalidates every approach that tries to detect auto mode at startup before
MFP is running.

---

## What Was Implemented (Currently in Commit `5516e5b`)

After discovering the hardware constraint, the solution shifted to two
independent detection paths that both converge at the same check point:

### Path 1 — Shutdown persistence
`DispatchLoopRunner.stop()` writes `"1"` or `"0"` to
`state/genau_mode_at_shutdown.txt` before MFP is killed. This reliably
captures the end-of-session state (the runtime value, not the mode file).

**Note:** This uncommitted piece was reverted. The committed piece below is
what remains.

### Path 2 — Live broker detection during startup (committed, not reverted)
`should_start_in_genau_mode()` was added to `windows_bridge_orchestrator.py`
(in commit `5516e5b`, bundled with a loading screen fix by the Opus agent).
It reads `bridge_config.genau_mode_file` and
`state/genau_mode_at_shutdown.txt`. The call is placed right before the
dispatch loop starts — after `run_startup_sequence()` has already run for
~15-20 seconds, during which MFP is live and the broker can receive the auto
mode serial message. If either file is "1", Primary VLC is paused via
`ensure_playback_state`.

**Why this still may not work:** The `genau_mode.txt` file is written by
the broker, which resets it to "0" at startup and then re-sets it to "1" only
after receiving "Auto mode is on!" from the OSR2. The OSR2 only sends this
message after receiving enough T-code to have confirmed auto mode is running.
In testing the feature was never verified to have worked end-to-end. The broker
detection path may in practice still read "0" if the timing doesn't work out.

---

## State of the Codebase

- **Committed and still present:** `should_start_in_genau_mode()` in
  `windows_bridge_orchestrator.py`, plus the two imports
  (`read_flag_file`, `ensure_playback_state`). These are in commit `5516e5b`
  and are harmless if the feature is re-implemented differently — the function
  simply returns `False` if both files are absent or "0".

- **Reverted:** `DispatchLoopRunner.stop()` shutdown persistence, and all
  associated tests.

---

## Recommended Starting Point for Next Agent

The core constraint is that you cannot detect auto mode before MFP is
sending T-code. The two viable approaches are:

1. **Shutdown persistence only.** Write the runtime `genau_mode` state
   somewhere durable (NOT the broker's mode file) when the session ends.
   Read it at next startup to decide whether to pause Primary VLC. This only
   helps for the "previous session was in auto mode" case.

2. **Post-startup polling.** After the loading screen completes and the
   dispatch loop starts, poll the broker mode file for a few seconds and
   transition to Genau if it flips to "1". This handles the cold-start
   case (user pressed auto button before starting Fun Time). The transition
   machinery already exists in the dispatch loop — this may just be a matter
   of the dispatch loop acting on the mode file flip the same way it does
   during a live session.

The dispatch loop already handles live auto-mode detection correctly (the user
confirmed: pressing the button during a live session immediately activates
Genau). So the live-session path is correct. The gap is only at the
moment `run_python_orchestrated_bridge` calls `ensure_playback_state` —
at that point the broker may not yet have received the serial message.

Approach 2 may not require any orchestrator-level change at all — just verify
that the dispatch loop's existing mode-file polling logic fires early enough
after startup completes.
