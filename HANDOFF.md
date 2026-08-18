# Origenerator-in-Fun-Time — handoff

## The three branches

| repo | branch | worktree |
|---|---|---|
| fun_time | `claude/origenerator-fun-time-47979b` | `fun_time/.claude/worktrees/genau-funscript-handoff-514881` |
| origenerator | `claude/origenerator-fun-time-mode` | `origenerator/.claude/worktrees/origenerator-fun-time-mode` |
| player_core | `claude/satellite-hud-shared-47979b` | `player_core/.claude/worktrees/satellite-hud-shared` |

All three are pushed to origin, all three trees are clean, and each is rebased
onto its own `origin/main` as of this handoff.

**player_core must land first** — the other two import the HUD it moves.

## What this is

Fun Time can hand its satellite half to Origenerator instead of its two video
players: `--fun-time` puts Origenerator's main window on the Random Favs
Browser's rect and its shows on the two satellite regions, driven by the same
file channels the session drives its players with.

## Running it

`python -m fun_time.branch_session --shortcut` from the fun_time worktree
writes `Verify <branch>.lnk` into the primary checkout and prints the checkouts
the next launch will carry. The two overrides that point a session at these
branches live in the fun_time worktree's git-ignored `state/`:

- `state/genau_project_dirs.txt` → the player_core worktree above
- `state/origenerator_dir.txt` → the origenerator worktree above

The origenerator worktree also needs its own `content.local.json` (git-ignored);
copy the primary's whenever main adds a key, or `test_paths` goes red on the new
config.

## Tests

- origenerator: `.venv/Scripts/python.exe -m pytest tests` from the origenerator
  worktree, with `PYTHONPATH` = fun_time worktree ; player_core worktree ;
  `workspace/haglio/app_support`. 2597 pass.
- fun_time units: `.venv/Scripts/python.exe -m pytest` (no PYTHONPATH). 2017 pass.
- fun_time integration: `python -m tests.integration.hidden_desktop`. 57 pass;
  `test_vr_player_integration` flakes under load and passes on its own.
- player_core: `pytest tests` with `PYTHONPATH` = the player_core worktree. 624 pass.

## Still owed (his words, in his order)

1. **The loading screen reveals too early.** Reported five or six times; three
   different fixes have failed. What has been tried: holding the curtain for the
   hosted window to exist; moving the whole banding pass behind the curtain (it
   used to run after it, so the room sorted itself out in front of him); waiting
   for both satellites' status files to report `position_ms > 0`. He says the
   windows are still not ready when the curtain lifts. Needs a genuinely
   different angle, not a fourth variation.
2. **The RFB flashes over Origenerator core when OmniPause is released.** Taking
   the browser out of the topmost band in origenerator mode (`role_topmost`) did
   not remove it, so it is elsewhere in the OmniPause-leave path.
3. **Split the entire TOC into two copies, Portrait and Landscape.** His actual
   solution to mixed-shape slideshows: selecting Videos under the Portrait copy
   and pressing slideshow must send it to the portrait viewer. The three shelves
   that currently carry Portrait/Landscape subfolders are the half-measure this
   replaces — he does not want Requests, Trash and All subdivided that way.
4. **Ken Burns on slideshow images**, zooming more slowly when the dwell is
   longer than the standard 4s.
5. **The standalone app's slideshow should wear the same Fun Time HUD**, and the
   bottom-center text (Requests, Enhancements) should move to the Fun Time toast
   style at top center.
6. **"landscape favorites" must turn on the HUD's F-mode and filter**, not open
   the shelf as an ordinary folder.
7. **The show's HUD still says "Shuffle" after "latest"** — the HUDs are supposed
   to behave identically in player mode and origenerator mode.
8. **OmniPause must stop videos playing in Origenerator core.** The looping
   thumbnails now stop; the videos in the info pane / generate tabs do not.
9. **A phrase recognized twice can come back "unrecognized" the third time** —
   Fun Time's recognizer, not the hosted app's routing.
10. **Icon assets should be standardized in shared_ui** (a task chip for this is
    already running in another session).

## Working notes for whoever picks this up

- He requires the nested-inline reply format on every message, and it is the
  thing this session failed at worst: quote his points at `>>`, answer at `>`,
  one blank line between threads and none inside one, carry every thread he
  keeps, never elide his history.
- Hand him a **clickable launcher link**, not a `file:///` one:
  `[▶ Launch <name>](http://127.0.0.1:41777/launch?t=a780245a4cdcfcb2a2e3b365&p=<percent-encoded-path>)`.
- Do not report "not done" and hand the turn back; finish the work first.
