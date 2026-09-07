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

### The headset's half

The headset's cover is drawn by the VR player, and the player is the last thing
a teardown kills -- so leaving the headset covered means keeping the player
alive past its own session. Its roles hold the very status and command files the
arriving desktop session claims, which is why the hold is a handshake rather
than a delay:

1. The VR teardown writes `vr_headset_hold.flag`, carrying whether this session
   started the VR runtime -- the orchestrator is the only thing that knows, and
   it is about to exit.
2. The player breaks its frame loop, stops both worker threads and closes every
   unit but the cover. Only then does it answer with `vr_headset_held.flag`.
3. The orchestrator waits for that answer before letting go. Without it, it
   closes the player exactly as it always did -- a hold that cannot be taken is
   never worth a session that will not start.
4. The player keeps presenting the cover, which reads "Returning to Fun Time..."
   and is exempt from the staleness rule the others obey: nothing is writing its
   progress file, because the session that would have is gone.
5. The arriving desktop session deletes the flag at its reveal, the moment the
   room is on its monitors. The player then closes its XR session and, if the
   flag said so, stops the runtime.

A relay whose crossing failed releases the hold too, and the player gives up on
its own after three minutes: a headset under a panel forever is worse than the
runtime's own view.

## The taskbar

There is one pinned button now, and one `AppUserModelID` under it
(`fun_time.win32_taskbar`). The VR player's windows claim Fun Time's identity,
so the headset session lights the button you already have. `launch_vr.vbs` stays
as the direct way to start a VR session on the installed config, and as one of
the launchers `tests/test_launch_smoke.py` reads to decide what to import-check;
it is simply not something to pin any more.

## The hosted app is not booted twice

Origenerator is the longest thing a desktop startup waits on: the curtain is
held up to forty seconds for it to answer. A crossing used to pay that twice,
because the desktop session closed it on the way out and the session coming
back launched a new one — no faster the second time, it being a fresh boot.

So a crossing keeps it. The teardown minimizes its window and records
`(pid, created_at)` in `origenerator_kept.txt` instead of closing it, leaves it
out of the kill sweep, and the arriving session adopts it: its window is
restored under the cover with every other window, and only the boot is skipped.
Identity is the pair and never the pid alone, because Windows hands freed pids
straight back out.

Its status file is deliberately left alone on adoption — the app is already
answering through it, and clearing it would buy back the forty seconds this
saves. The paused flag and the command file are cleared as ever: a stale freeze
or an unread verb from the last session would land on this one.

Whoever ends up with nothing to hand it to closes it: a VR session quitting
rather than crossing back, and a relay whose crossing failed. A record whose
process is gone is simply forgotten.

## What each cover waits on

Every cover reads a progress file and gives up on one that stops moving, so
that a headset or a monitor is never left under a panel nothing will ever take
down. The timeouts differ because what they are waiting out differs:

- **The VR startup cover** (`STARTUP_STALE_TIMEOUT_S`, 120s) matches the
  orchestrator's own patience with the player: startup's last phase writes
  nothing for the length of it, and the cover cannot appear at all before the
  player holds an OpenXR session, well into "Waiting for players...".
- **The VR shutdown cover** (20s) covers a teardown that is over in seconds.
- **A held cover** is exempt: nothing is writing its file, because the session
  that would have is gone.
- **The crossing cover** on the monitors allows minutes, because a crossing
  takes them and its own relay bounds it far shorter.

`SceneReady` is a different question — whether the room is on screen, which a
desktop orchestrator sees for itself and a headset cannot report. Its grace
(25s) mostly waits out the headset being picked up: a launch is over in six
seconds and nobody is wearing it by then. The dwell (2s) is how long the cover
must be in front of a WORN headset before the room may be revealed; without one
the loading screen was over before anyone had it on, which is what its first
verifications saw — nothing at all.
