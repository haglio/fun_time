# Entering VR

Fun Time and FunTimeVR are one session in two shapes. Both read the same config
and the same state dir, run the same dispatch loop, voice control and AHK
bridge, and drive the same OSR2 broker; the difference is that a VR session
hosts every visual role inside one VR player process instead of Nau, Genau and
two satellite windows. So "Fun Time VR" is not a second app to start, and since
2026-09-07 it is not a second thing to click either: you say **"enter VR"** and
**"exit VR"**, the session running ends, and the other one opens on the state it
leaves.

Recorded here rather than in module docstrings, for the reason
[known-issues.md](known-issues.md) gives: the same design written out at four
call sites is free to drift apart at three of them.

## What carries across

Both apps have always shared the state dir, so most of a session already
crossed. `fun_time.session_resume` is what puts it back, and it runs identically
in either orchestrator:

| | Carries |
|---|---|
| Both satellites' playlists, rotated onto the clip each had on screen | yes — the two libraries are the same in either app |
| Each satellite's lock | yes, re-queued on its command file |
| F-mode per player, each side's filter, browse order, group loops, map anchors | yes, via `resume_shared_state` |
| Sound level and mute | yes |
| The main slot's mode (video / Genau) and the satellites' (video / Origenerator) | yes |
| The main player's clip | yes, when the arriving session can play it |
| The main player's A/B loop | only when the clip carried and the arriving app has A/B loops |
| OmniPause | no — a session never opens paused |

The main player is the one that needed work, and it is why this note exists.
Each app refuses the other's main playlist: the desktop must never put a
VR-mastered video on the primary monitor, and a desktop rotation resumed into a
headset is nothing but flat screens. So a crossing rebuilds that one playlist
from the arriving session's own sources — and rebuilding *alone* threw away the
clip you were watching, in both directions, every time, while both satellites
came back exactly as you left them.

`session_resume.resume_main_video` is the other half: after the rebuild, the
playlist is rotated onto the clip that was on screen. You keep watching what you
were watching, and the queue under it is the arriving library — so entering VR
carries the video into the headset and gives you VR video the moment it moves
on.

It cannot always be kept. A VR master on the way back to the desktop is not in
the desktop's rebuild, because the desktop cannot play it; that crossing opens
on the rebuild's own first clip. The log line says which of the two happened.

The A/B loop follows the clip, and only on the desktop: the VR main role does
not implement `SET_LOOP` (see [known-issues.md](known-issues.md)), so a loop
does not survive a stay in the headset.

## How the crossing runs

Both orchestrators hold the same single-instance mutex for their whole life, so
one cannot simply start the other — the incoming session would find the mutex
held and turn itself away with "already running". Nor can the outgoing one wait
for its own exit.

So the crossing goes through a relay, `fun_time.session_handoff`:

1. The dispatch loop answers `enter_vr` / `exit_vr` by writing
   `state/session_handoff.txt` and then ending the session exactly as `quit`
   does — "exit" on the AHK command channel. Said of the session already
   running, it posts a notice and stays put instead.
2. The orchestrator's `main`, after teardown and as its very last act, takes
   that request off the disk and spawns the relay, detached.
3. The relay waits for the mutex to come free. That is the outgoing session
   having actually let go — every player killed, the AHK bridge gone, the state
   files written — not merely having been asked to stop.
4. It starts the incoming orchestrator on the same config, with the outgoing
   session's working directory, so a branch session crosses into the branch's
   own code rather than into the primary's.
5. It then watches that session's own startup marker (`launcher.ready` /
   `vr_launcher.ready`) the way the app's `.vbs` launcher would, since nothing
   else is watching this one. A crash is reported the moment the process goes, a
   wedge on the timeout, and either pops a dialog with the tail of the launcher
   log — the alternative being an empty desktop and no explanation.

A request left on disk by a session that crashed before it could take its own is
cleared at every startup: it describes one ending, and obeyed later it would
send an ordinary launch into the headset on its way out.

### A crossing phrase must not begin with a phrase of its own

"exit" was a spoken synonym for "quit". The first time "exit VR" was ever said,
the room quit instead: both were whole phrases in the grammar, and the shorter
one scored better — the event log recorded `Voice command: quit`, the AHK
channel took an exit, and no crossing was ever requested. "enter VR" was never
at risk, because no bare "enter" was in the grammar to win.

So the synonym is gone. "quit" still quits, and so does Ctrl+Alt+Q. A stray
"exit" now lands on the crossing pair instead, which in the session it names is
answered with a notice and nothing else — a far cheaper misreading than ending
the room. `tests/test_voice_commands.py` holds the rule for every crossing
phrase, present and future.

Nothing has to be handed over besides the request. The broker keeps running
across the crossing as it does across any restart, the AHK bridge is
`#SingleInstance Force` and the outgoing one is gone before the incoming one
starts, and everything the two sessions share is already on disk.

## Nothing is ever uncovered

A crossing tears one session down and builds another, and the covers each
session raises go with it — so the monitors went bare between them, and the
first thing "enter VR" showed was the desktop closing and FunTimeVR starting up
in the space it left.

The crossing cover is what spans that stretch. It is raised by the session that
is LEAVING and taken down by the one that ARRIVES, which is the one thing the
closing screen cannot do:

- **Entering VR.** The desktop's teardown raises it instead of its closing
  screen, saying "Entering VR...", and leaves it standing. FunTimeVR's own
  loading cover comes up in the headset meanwhile, so both devices are covered
  at once. The cover comes down when the VR session reveals — the headset is
  showing content by then, which is the moment it was waiting for.
- **Leaving VR.** FunTimeVR's teardown cover hangs in the headset, so the
  monitors get a crossing cover of their own, saying "Returning to Fun Time...".
  The arriving desktop session hands over to its own loading screen the moment
  that is painted, rather than at its reveal, where a crossing cover would spend
  the whole startup sitting on top of it.

`fun_time.transition_screen` is the cover itself; `fun_time.session_handoff`
raises and drops it. A relay whose crossing failed drops it too, so a session
that never arrived does not leave the monitors covered; the cover's own
staleness timeout is the backstop for a relay that died outright.

What is NOT covered is the headset on the way out: the VR cover is drawn by the
VR player, and the player is the last thing the teardown kills. Holding it past
that would mean keeping the player alive — and its roles hold the very status
and command files the arriving desktop session claims — so the headset shows the
runtime's own environment for the few seconds until Fun Time is up.

## The taskbar

There is one pinned button now, and one `AppUserModelID` under it
(`fun_time.win32_taskbar`). The VR player's windows claim Fun Time's identity,
so the headset session lights the button you already have. `launch_vr.vbs` stays
as the direct way to start a VR session on the installed config, and as one of
the launchers `tests/test_launch_smoke.py` reads to decide what to import-check;
it is simply not something to pin any more.
