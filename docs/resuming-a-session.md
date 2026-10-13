# Resuming a session

`fun_time.session_resume` brings a reopened session back to the clip each Funestra
was on, and the mode they were in. Both orchestrators call it, so a desktop
reopen, a VR reopen and a crossing between the two
([entering-vr.md](entering-vr.md)) all resume the same way.

The design lives here rather than in the module, for the reason
[known-issues.md](known-issues.md) gives for its own deferrals: written out
across a dozen docstrings it is free to drift apart in eleven of them.

## Why it exists

Every Funestra starts at the top of the playlist file fun_time hands it, and
startup used to overwrite all three with a fresh weighted shuffle — so reopening
Fun Time landed on three clips you had never chosen and lost whatever you were
watching. Resume keeps the satellites' last playlists and rotates each onto the
clip that was on screen: the Funestra's first entry is where you left off, and
because a playlist wraps, the clips that were coming up still come up in the
same order. The Main Funestra's list is never kept; it is built again, from the
video that was on screen ([below](#the-main-funestras-one-list)).

Keeping those files means keeping what *shaped* them. A playlist carries its
session's F-mode, filter, order and loop in it, so the session has to come back
believing what its files say, or every HUD describes a session other than the
one playing.

Nothing is written at shutdown for either half. Each Funestra publishes the video
it is playing to its status file every tick, and the dispatch loop writes the
state file after every command, so the last tick before the session ended is the
record — one that survives the force-kill that ends a session, and a crash or a
power cut too, where a shutdown hook would not.

## What comes back, and why

`RESUMED_FIELDS` is the list. Most of it is what shaped the playlist files: each
Funestra's own F-mode and each side's filter decide which clips are in them,
Latest fixes their order, a group loop *is* the group written out as the
playlist, with the map anchored (and the seed row widened) on the clip it
started from, and the Main Funestra's buttons, with the number its shuffle is
dealt from, are what its one list is built from. The rest is what the session was simply *left* in —
the sound level, the OSR2's max intensity, each side's lock, whether the OSR2
was let go of or held at one end, whether Genau was browsing Latest — and there
is no more reason for those to reset overnight than for the clip on screen to.

Some of them have a live counterpart to re-assert, since none of those lives in
a file a new process reads:

- the sound level is seeded to both audio sinks at startup
  (`fun_time.audio_volume.publish_audio_level`),
- the OSR2's max intensity, held below full, is seeded at startup to every
  Funestra that can drive the OSR2 (`fun_time.max_intensity.publish_max_intensity`),
- each satellite lock is queued back on that satellite's command file
  (`resume_satellite_locks`),
- Genau's choice between its VR and flat flicks is queued back on Genau's
  command file in the headset (`fun_time_vr.orchestrator.resume_genau_shapes`), because Genau rescans
  its folders whole at every launch; a desktop Genau has only flat flicks, so
  nothing is sent to it,
- the main slot's mode is what startup seeds Kino, Genau and the Main Funestra's
  window for (`fun_time.windows_bridge_startup.seed_startup_states`),
- Genau's order goes on its launch line (`--latest`), beside the flick it opens
  on. Genau has no playlist file — it rescans its folder every launch, in the
  config's order unless told otherwise — and the LATEST verb is no way to tell
  it, since that browses the new order from its top and would throw away the
  flick just resumed. The headset's Genau is handed the same two at construction,
- the OSR2's control state is carried out by the device arbiter
  (`fun_time.device_arbiter`) on the new session's first tick and every tick
  after, exactly as it is after a press. Startup sends the device home first,
  which is where control off leaves it anyway.

Carrying a flag whose world is not put back with it is the same lie as dropping
one that was true.

Nothing else survives, because nothing carries it into the new session:
OmniPause's paused flags are cleared before the Funestras launch, and a
keyboard-navigation selection was never a thing you could leave running.

Nor does which Funestra has most of the secondary monitor: startup opens both
windows where they always open, and the session's first ticks seat them from
the crown, which does come back, and the shape of what the Main Funestra shows,
whether that is a video or one of Genau's flicks.

Whatever Origenerator held comes back — each Satellite Funestra's own
Origenerator mode, and Origenerator in the Random Favs Browser's place — and it
comes back UNDER THE COVER. Every room is built with both Funestras in Kino mode
and the browser in its place, so a room left otherwise has to be switched back,
and switching it once the room is already his is what this must never do: the
Funestras would rearrange themselves under whatever he had started, which was
tried and rejected. So the loading screen stays up instead. The phase before its
last, read on the cover as "Activating Origenerator...", comes after the
room is built and banded and waits for the hosted Origenerator to publish its
status — re-written while it waits, since a cover takes itself down on a progress
file nothing has touched for a minute (`overlay_progress`). The moment the app
answers, the very commands the switches send are sent, one for each thing
Origenerator held, and only then does the cover come down — on a room already
the way it was left. A room Origenerator held nothing of waits for nothing.

The wait is given up on after `ORIGENERATOR_BOOT_BUDGET_S`, because an app that
died on its way up would otherwise hold the room shut; a room that gave up opens
with Origenerator holding nothing and stays that way for the session, and the
state file is corrected to say so, since that is what the satellite HUDs and the
dashboard's strip draw and what the switches act on. An Origenerator that was
already open when the session began — one he opened himself and the session took
over, or one a crossing kept — has no boot to wait out, so what it held is there
as the cover lifts.

A room also opens with Origenerator in the browser's place and on one satellite,
whatever the last one was left in, when the Origenerator it takes over is showing
a slideshow: he was watching it, so the room comes up with that show on the
satellite of the monitor it was on, and the other satellite as the last session
left it (his call, 2026-10-13). The app holds the show through the takeover and
plays it there when that side's show is asked for. It says it is showing one
beside its offer, in `fun_time_showing.txt`, naming its own process the way the
offer does, so the mark of an app that has since closed is never believed, and
then the monitor's shape, `portrait` or `landscape`; a mark naming no monitor is
from an app older than that, and puts Origenerator on both satellites.

Coming back to a Funestra's Origenerator mode means putting back what the mode took. While the hosted app
has a satellite, that Funestra's playlist file holds the app's pictures and the
session's own list waits beside it (`fun_time.funestra_handover`). A session quit
there, or killed there, never brings the Funestra home — so `resume_playlists`
takes every kept list back before it reads anything, and a room built in video
mode opens on video lists, at the clip each side was showing when it left.
Without that, the session after a quit in Origenerator mode opened in Kino mode
on two locked pictures, each with a one-square map and nothing to step to
(2026-09-19). The kept lists are taken back even when the resume goes on to find
nothing to resume: one that outlived the rebuild would be dealt over it the next
time that Funestra came home.

## The Main Funestra's one list

The Main Funestra plays one list, and `fun_time.main_list` is the one place it is
built. It is built from the Main Funestra's buttons — 2D and VR, clips and full
videos, F-mode, Latest or Shuffle — every time one of them is pressed and every
time a session starts, and no session reads back the list the last one left: a
kept list is one more thing that can disagree with the buttons, and with the
session's own library, which in the headset is the VR library and the desktop's
together (2026-10-11: a headset session that kept the last list played a 2D
video after Next with only VR lit).

Every build starts from the video on screen. The whole library is put in its
order — newest first under Latest, shuffled otherwise — turned round so that
video comes first, and narrowed to what the buttons let through. A press that
still lets that video through keeps it playing, and one that does not moves on
to the next video it does let through, in the order they were coming up in.

The shuffle is dealt from a number kept in the state file
(`main_shuffle_seed`), not from chance at every build: the same number puts the
same videos in the same order, so narrowing and widening never reshuffle, and a
reopened session's videos come up in the order they were coming up in, with any
that arrived since dealt in among them. Shuffle deals a new number, and so do
the reset and a session that starts fresh.

When a press leaves the buttons letting nothing through -- both 2D and VR off,
both lengths off, or a mix no video passes, such as only VR with only clips --
the build answers it one way, whichever press it was: the list is the video on
screen alone, the Main Funestra is locked on it, and the room says "No videos
left". A session that starts with nothing let through holds the video on screen
the same way; with no video to hold, it puts the buttons back where a fresh
session has them, keeping the shuffle's number.

Whether a video is a clip or a full video is the kind Evolver recorded for it.
A video with no record counts as a full video, and so does every video in a VR
folder, which is never asked about at all: the VR library is on the cloud drive,
where one question about one file can hang for hours.

## The things that have to be re-sent

A satellite's lock is repeat-one in mpv's own `loop_file`, and the Main Funestra's
A/B loop is a range inside one video. Both live in a Funestra process that has
just been replaced, so unlike a filter or an order neither can ride back in on a
file the new Funestra reads — each is queued on the Funestra's command file before
that Funestra launches, and drains on its first tick. By then the Funestra has
loaded the clip at the top of its playlist, which is the clip the
lock was on and the video the loop was cut from. The Main Funestra holds the seek until mpv
has the file open (its `restore_loop`), so the loop lands however slowly the
file opens.

The loop is only handed back when the Main Funestra really did come back onto the
video it was cut from. A video deleted since, or one the arriving session cannot
play, leaves some other video leading, and those bounds would then mark out a
stretch of a video nobody chose — which is what `playlist_opens_on` is asked
before the queue.

## What a reopen re-sends because it lives in a Funestra

Three more things live only in a Funestra process, so they are gone the moment a
session ends: each Funestra's playback rate, the Main Funestra's hold on the video it
is playing, and Genau's Amp, Speed and Center bars, shape, flick seconds and three switches. Every
session re-sends all of them off the status and drive files the last one
published, so the room that opens is the room the last one was showing, whether
it was left by a crossing ([entering-vr.md](entering-vr.md)) or by quitting. The
mic goes the same way: the mute lives in the controller this session runs, so the
dispatch loop writes it down whenever it moves and the next session starts its own
controller muted.

## Two rules that are easy to lose

**Resuming the satellites' playlists is all or nothing.** One build writes
both, so both rotations are worked out before either is written: a session
either resumes whole or is left exactly as the last build wrote it.

**The state file is written either way.** The alternative was deleting it at
startup, which is exactly how a session came back playing favorites while every
HUD said F-mode was off — and then answered "F-mode" by reporting it *enabled*
and changing nothing you could see. Writing defaults clears a crashed session's
leftovers just as the delete did, and the state carried forward is only ever the
state that explains the files on disk: a session built fresh, or rebuilt over
the top, opens on defaults.

## Where the video was left

Resume puts the clip that was on screen back at the top of each playlist; the
point inside that clip is the Funestras' own, and is kept for every video rather
than only the one a session ended on. `main_funestra.play_points` is the whole of
it — a JSON file in the state dir keyed by video path, read when a Funestra opens
a file and written as it plays. Every Funestra that shows a video takes it: the
Main Funestra (with `main_funestra.kino.Kino` on it), both satellites (each one
the satellite program's window), and the headset's three, which are the same
Funestras again.

Each Funestra keeps its own file — `<who>_play_points.json`. One file between them
would have each Funestra's whole-file write erasing what the others had added
since it started, which is the lost update `app_support.json_store` exists for
elsewhere; separate files need no lock, and no clip is played by two of them.

Every video is remembered, whatever its length, and wherever in it the playhead
was. There is no minimum watched and no "near enough to the end to count as
finished": the library is mostly short videos, and a threshold at either end
would have excluded most of it. Two things clear a point rather than move it: a
video wound back to its very top, which is the same thing as opening at the top,
and a video that ran out — the end of the file, exactly, not a window near it.
Without that second one a satellite would be unplayable: its clips auto-advance
at end-of-file, so every clip it had ever shown would open at its last frame and
step straight on, and the playlist would race.

Leaving a video — stepping off it, or closing the Funestra — writes the exact
spot, so coming back lands where you left rather than near it. On top of that it
is written every `WRITE_EVERY_S` while the video plays: a session is as often
killed as closed (above), and that periodic record is what a killed one comes
back on.

One more number is not a preference. Only a tick whose clock moved forward by
less than `PLAYED_ON_MS` is written down periodically — a larger jump is a seek,
a wrap at end-of-file, or the clip just left, whose position mpv goes on
reporting for a tick or two after it is told to open another. And `REMEMBERED`
videos keep a point, the least recently watched dropping off the end, so a file
rewritten while a video plays cannot grow without bound.

The seek is held until the Funestra reports a duration, the way the restored A/B
loop above is, so a video opens *at* its point rather than visibly jumping there
once the file is up. A satellite rolls onto its prefetched next clip by itself,
so there the seek lands a tick or two into the clip rather than before it.
