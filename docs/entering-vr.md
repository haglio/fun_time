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

Nothing has to be handed over besides the request. The broker keeps running
across the crossing as it does across any restart, the AHK bridge is
`#SingleInstance Force` and the outgoing one is gone before the incoming one
starts, and everything the two sessions share is already on disk.

## The taskbar

There is one pinned button now, and one `AppUserModelID` under it
(`fun_time.win32_taskbar`). The VR player's windows claim Fun Time's identity,
so the headset session lights the button you already have. `launch_vr.vbs` stays
as the direct way to start a VR session on the installed config, and as one of
the launchers `tests/test_launch_smoke.py` reads to decide what to import-check;
it is simply not something to pin any more.
