# Resuming a session

`fun_time.session_resume` brings a reopened session back to the clip each player
was on, and the mode they were in. Both orchestrators call it, so a desktop
reopen, a VR reopen and a crossing between the two
([entering-vr.md](entering-vr.md)) all resume the same way.

The design lives here rather than in the module, for the reason
[known-issues.md](known-issues.md) gives for its own deferrals: written out
across a dozen docstrings it is free to drift apart in eleven of them.

## Why it exists

Every player starts at the top of the playlist file fun_time hands it, and
startup used to overwrite all three with a fresh weighted shuffle — so reopening
Fun Time landed on three clips you had never chosen and lost whatever you were
watching. Resume keeps last session's playlists and rotates each onto the clip
that was on screen: the player's first entry is where you left off, and because
a playlist wraps, the clips that were coming up still come up in the same order.

Keeping those files means keeping what *shaped* them. A playlist carries its
session's F-mode, filter, order and loop in it, so the session has to come back
believing what its files say, or every HUD describes a session other than the
one playing.

Nothing is written at shutdown for either half. Each player publishes the video
it is playing to its status file every tick, and the dispatch loop writes the
state file after every command, so the last tick before the session ended is the
record — one that survives the force-kill that ends a session, and a crash or a
power cut too, where a shutdown hook would not.

## What comes back, and why

`RESUMED_FIELDS` is the list. Most of it is what shaped the playlist files that
were just resumed: each player's own F-mode and each side's filter decide which
clips are in them, Latest fixes their order, and a group loop *is* the group
written out as the playlist, with the map anchored (and the seed row widened) on
the clip it started from. The rest is what the session was simply *left* in —
the sound level, each side's lock, whether the OSR2 was let go of or held at one
end — and there is no more reason for those to reset overnight than for the clip
on screen to.

Four of them have a live counterpart to re-assert, since none lives in a file a
new process reads:

- the sound level is seeded to both audio sinks at startup
  (`fun_time.audio_volume.publish_audio_level`),
- each satellite lock is queued back on that satellite's command file
  (`resume_satellite_locks`),
- the main slot's mode is what startup seeds the two main-slot players and their
  windows for (`fun_time.windows_bridge_startup.seed_startup_states`),
- the OSR2's control state is carried out by the device arbiter
  (`fun_time.device_arbiter`) on the new session's first tick and every tick
  after, exactly as it is after a press. Startup sends the device home first,
  which is where control off leaves it anyway.

Carrying a flag whose world is not put back with it is the same lie as dropping
one that was true.

Nothing else survives, because nothing carries it into the new session:
OmniPause's paused flags are cleared before the players launch, and a
keyboard-navigation selection was never a thing you could leave running.

The satellite side's mode is dropped on purpose rather than for want of a way
to carry it. Every room is built in video mode, because the hosted Origenerator
that origenerator mode is made of is still booting when the room opens and
nothing waits for it any more. Coming back to the mode LATER, once that app
answered, was tried and is worse: the two sides would rearrange themselves under
whatever had been started in video mode. So being in origenerator mode is simply
not something a session remembers — and until the app is up the mode cannot be
entered at all: the switch answers "Origenerator is still starting", and both
satellite HUDs draw that button dim. An Origenerator that was already open when
the session began — one he opened himself and the session took over, or one a
crossing kept — has no boot left to wait out, so its mode is open from the start.

## The things that have to be re-sent

A satellite's lock is repeat-one in mpv's own `loop_file`, and the main player's
A/B loop is a range inside one video. Both live in a player process that has
just been replaced, so unlike a filter or an order neither can ride back in on a
file the new player reads — each is queued on the player's command file before
that player launches, and drains on its first tick. By then the player has
loaded the clip the resume put at the top of its playlist, which is the clip the
lock was on and the video the loop was cut from. The main player holds the seek until mpv
has the file open (its `restore_loop`), so the loop lands however slowly the
file opens.

The loop is only handed back when the main player really did come back onto the
video it was cut from. A rebuilt playlist, or a clip deleted since, leaves some
other video leading, and those bounds would then mark out a stretch of a video
nobody chose — which is what `playlist_opens_on` is asked before the queue.

## Two rules that are easy to lose

**Resuming the playlists is all or nothing.** One build writes all three, so
every rotation is worked out before any of them is written: a session either
resumes whole or is left exactly as the last build wrote it.

**The state file is written either way.** The alternative was deleting it at
startup, which is exactly how a session came back playing favorites while every
HUD said F-mode was off — and then answered "F-mode" by reporting it *enabled*
and changing nothing you could see. Writing defaults clears a crashed session's
leftovers just as the delete did, and the state carried forward is only ever the
state that explains the files on disk: a session built fresh, or rebuilt over
the top, opens on defaults.

## Where the video was left

Resume puts the clip that was on screen back at the top of each playlist; the
point inside that clip is the players' own, and is kept for every video rather
than only the one a session ended on. `main_player.play_points` is the whole of
it — a JSON file in the state dir keyed by video path, read when a player opens
a file and written as it plays. Every player that shows a video takes it: the
main player (`main_player.session.PlayerSession`), both satellites
(`satellite.session.SatelliteSession`), and their headset twins
(`fun_time_vr.roles.MainRole` and the same satellite session again).

Each player keeps its own file — `<who>_play_points.json`. One file between them
would have each player's whole-file write erasing what the others had added
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

Leaving a video — stepping off it, or closing the player — writes the exact
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

The seek is held until the player reports a duration, the way the restored A/B
loop above is, so a video opens *at* its point rather than visibly jumping there
once the file is up. A satellite rolls onto its prefetched next clip by itself,
so there the seek lands a tick or two into the clip rather than before it.
