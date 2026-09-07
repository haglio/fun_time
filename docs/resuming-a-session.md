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
the sound level, each side's lock — and there is no more reason for those to
reset overnight than for the clip on screen to.

Three of them have a live counterpart to re-assert, since none lives in a file a
new process reads:

- the sound level is seeded to both audio sinks at startup
  (`fun_time.audio_volume.publish_audio_level`),
- each satellite lock is queued back on that satellite's command file
  (`resume_satellite_locks`),
- the main slot's mode is what startup seeds the two main-slot players and their
  windows for (`fun_time.windows_bridge_startup.seed_startup_states`).

Carrying a flag whose world is not put back with it is the same lie as dropping
one that was true.

Nothing else survives, because nothing carries it into the new session:
OmniPause's paused flags are cleared before the players launch, and a
keyboard-navigation selection was never a thing you could leave running.

## The things that have to be re-sent

A satellite's lock is repeat-one in mpv's own `loop_file`, and the main player's
A/B loop is a range inside one video. Both live in a player process that has
just been replaced, so unlike a filter or an order neither can ride back in on a
file the new player reads — each is queued on the player's command file before
that player launches, and drains on its first tick. By then the player has
loaded the clip the resume put at the top of its playlist, which is the clip the
lock was on and the video the loop was cut from. Nau holds the seek until mpv
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
