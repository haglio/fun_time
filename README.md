# Fun Time

Fun Time is a Windows desktop setup that launches and coordinates:

- Nau, a funscript video player for the main player's video library (lives in the separate `../genau` project, launched as `python -m nau`)
- two satellite VLC instances (portrait and landscape)
- Genau, a clip-based visualizer for OSR2 auto mode (the separate `../genau` project)
- a Genau audio companion
- a minimal AutoHotkey hotkey shell (window placement and command dispatch run in Python)

It uses a serial broker for the OSR2 — the separate `../broker` project — that is intended to run continuously in the background.

The main stack runs in one of three modes (startup mode is **nau**):

- in **Nau mode**, Nau owns the main player and the OSR2: it plays the whole main library (videos without a funscript just play with no OSR2 output) and drives the OSR2 itself by sending funscript-derived T-Code over UDP to the broker
- in **Genau mode** (OSR2 auto/free mode), Genau clips own both the main player and the OSR2
- in **Hybrid mode**, Nau displays video under Genau's HUD while Genau drives the OSR2

## Folder layout

Core files:

- `main.sh` — compatibility wrapper that forwards to `orchestrator.py`
- `launch.vbs` — hidden Windows launcher used by the shortcut/taskbar item
- `launch_branch.vbs` — the same launch, aimed at a branch worktree; run by the `Verify <branch>.lnk` an agent leaves (see “Verifying an unlanded branch”)
- `fun_time_config.json` — central config for paths, ports, and layout values
- `windows_bridge_hotkeys.ahk` — minimal AutoHotkey hotkey shell launched by the orchestrator
- `fun_time/` — shared Python package for config, logging, orchestration, the dashboard, and command dispatch
- `icon.ico` — Fun Time icon

Runtime state:

- `state/genau_mode.txt`
- `state/genau_paused.txt`
- `state/genau_cmd.txt`
- `state/nau_cmd.txt`
- `state/nau_paused.txt`
- `state/nau_status.txt`
- `state/nau_playlist.tsv`
- `state/audio_paused.txt`
- `state/*.log`

Local runtime data:

- `favs.csv` — favorites CSV written when a satellite VLC is locked
- `Fun Time.lnk` — convenience shortcut

## Recommended project-local paths

`favs.csv` should live inside the project folder, not in the old top-level location.

Recommended:

- `C:\path\to\fun_time\favs.csv`

It should be ignored by Git.

## Configuration

Runtime configuration is centralized in `fun_time_config.json`.

`fun_time/config.py` no longer supplies fallback defaults; this JSON file is the single source of truth and should include the full config.

This file now controls:

- executable paths
- media/library paths
- serial and UDP ports
- Genau playback defaults
- monitor/layout ratios used by the Python window layout

Useful sections:

- `paths`
- `vlc`
- `layout`
- `genau`
- `audio_companion`

For the satellite AI libraries, Fun Time can now read either a single folder or multiple folders:

- `paths.portrait_dir` or `paths.portrait_dirs`
- `paths.landscape_dir` or `paths.landscape_dirs`

If the list form is used, the portrait or landscape VLC gets all listed folders joined into one rotating source set.

Nau's video library folders are configured with `paths.nau_library_dirs` (a list of one or more folders):

Example:

```json
{
  "paths": {
    "nau_library_dirs": [
      "C:/videos/set_a",
      "C:/videos/set_b"
    ]
  }
}
```

`genau.shuffle_on_load` defaults to `true`, which randomizes clip order once at load time.

To disable shuffle and use filesystem order:

```json
{
  "genau": {
    "shuffle_on_load": false
  }
}
```

The layout values that used to be hard-coded in AutoHotkey now live under `layout`.

Monitor naming under `layout` now uses:

- `primary_monitor` — the monitor that shows the landscape VLC, the dashboard, and the Random Favs Browser
- `secondary_monitor` — the monitor that shows the portrait VLC and the shared main-player slot (Nau and Genau use the same rect)

## High-level architecture

Serial / mode control:

- the real OSR2 is on `COM4`; the **broker** — the separate `../broker` project — is the only process that talks to it. It forwards UDP T-Code to the OSR2 unconditionally and suppresses serial input while UDP flows, watches the OSR2 for free-mode transitions, and publishes mode/timing state over localhost and `state/genau_mode.txt`.
- **Nau** — a funscript video player in the `../genau` project — never opens `COM4`. It drives the OSR2 itself by sending funscript-derived T-Code to the broker over UDP (the same port Genau uses), reads commands from `state/nau_cmd.txt`, and publishes playback status to `state/nau_status.txt`.
- **Genau** — the separate `../genau` project — never opens `COM4` either. It follows the broker-fed state, shows itself only in Genau/Hybrid mode, and reads clip/offset commands from `state/genau_cmd.txt`.

See those projects for the serial parsing, COM-port recovery, and playback internals.

### The log panel

The main monitor's left column stacks the **Dashboard** across its top and the **Random Favs Browser** filling the rest below. The Dashboard spans the full column width: its schematic of the two monitors on the left and the **log panel** embedded in the strip beside it. The schematic still draws all three regions, so the picture matches the screen.

The log panel is a widget inside the dashboard window — one window, not two — so it rides the dashboard's topmost band, minimize/restore and close. It tails `state/event_log.jsonl` and shows the **stream** of everything the session logs, filtered by a verbosity dial (`DEBUG`/`INFO`/`NOTICE`/`WARNING`/`ERROR`, default `NOTICE`) and by per-window toggles across one compact row. Both settings persist in `state/log_panel.ini`.

The brief **notices** — "Clip saved", "No other seeds", "Next seed", "Similar clip" — flash over the top-center of the player they concern (a portrait notice over the portrait satellite, a main-player notice over the Nau/Genau display) and then fade. They also land in the stream, colored by level, so the panel is where you scroll back through them. The flash always fires regardless of the verbosity dial, which governs only the stream. Long lines in the stream **word-wrap** rather than being cut off, so the tail of a message (a video name, a phrase heard) is readable.

Every recognized voice command flashes a **green confirmation** — the phrase it matched — over the player it addresses, so you can see what was heard. A command that hits a dead end ("No other seeds", "No action metadata") flashes **red** instead. And when the recognizer clearly hears speech that matches no command, it flashes **"unrecognized voice command: ‹what it heard›"** in red — over the player the phrase named, if it named one ("landscape ‹something garbled›" reports on landscape, not the main player) — a second, unrestricted recognizer runs alongside the grammar one purely to transcribe that, so an out-of-grammar phrase surfaces as text instead of vanishing.

## Requirements

### Windows apps

- VLC
- AutoHotkey v2

### Python / tools

- Python (currently launched via Miniconda `pythonw.exe`)
- Python dependencies are declared in `pyproject.toml` — notably PyQt6 (dashboard), pygame-ce (audio companion), vosk + sounddevice (voice control), and Pillow / numpy / opencv-python.
- Genau and Nau run out of the `../genau` project's venv (`paths.genau_python_exe`), launched as `python -m genau` and `python -m nau`.

Install the declared dependencies into the project venv before first use.

## Launching

### Broker startup task (one-time setup)

The broker runs as its own background service from the `../broker` project — see that project for its one-time startup-task setup (it can autostart at Windows logon). Launching Fun Time also starts the broker tray if it is not already running.

### Normal way

Use the `Fun Time` shortcut / taskbar launcher, which calls:

- `launch.vbs`
- which runs `python -m fun_time.orchestrator`

`fun_time.orchestrator` now starts the broker tray launcher if the broker is missing, so the tray status icon and broker recovery flow stay aligned with Windows logon startup.

### Verifying an unlanded branch

Fun Time runs from the primary checkout, and that only moves when `main` does —
so work sitting on a branch is code you cannot otherwise see, run or judge.

There is nothing to pick. An agent with a branch to show runs `python -m
fun_time.branch_session --shortcut` from its worktree, which leaves a
`Verify <branch>.lnk` in this folder and tells you the filename; double-clicking
it runs a real session on that worktree's code — your real library, your real
monitors, uncommitted edits included. `launch_branch.vbs` is what those
shortcuts point at and is not run on its own. The agent takes its shortcut back
out once the work lands, and any left over from a worktree that has since been
deleted are swept away whenever a new one is written.

A branch session **replaces** the live one rather than running beside it. Almost
everything a session touches is one-per-machine — the AHK hotkey shell is
`#SingleInstance Force`, the UDP endpoints and the loopback port are fixed, and
there is one microphone, one broker and one set of monitors — so the generated
config carries the live session's `instance_id` and both take the same
single-instance mutex. Whichever starts second is turned away with the usual
"already running" message, including the taskbar icon while a branch session is
up. Quit with `Ctrl+Alt+Q` and launch normally again afterwards.

What the branch session keeps to itself is `state/`, inside the worktree — its
command files, playlists, logs, thumbnails and resume point. Everything else is
the real thing on purpose. See `fun_time/branch_session.py`.

### Clipper way

Clipper has been extracted to its own project at `../clipper`. See that project for usage details.

### Validation run

Validation only:

```powershell
python -m fun_time.orchestrator --check
```

### Direct full launch

From PowerShell:

```powershell
python -m fun_time.orchestrator
```

Alternative compatibility launch:

```bash
cd /path/to/fun_time && bash ./main.sh
```

The `--check` mode is the fastest way to validate config and path wiring before a full launch.

## Hotkeys & voice

Fun Time is driven by global hotkeys and, optionally, spoken voice commands. While Fun Time is running and not OmniPaused, the hotkeys are global — they fire regardless of which window is focused.

The complete, always-current list of keys and spoken phrases lives in the app. Click the **?** button on the dashboard (tooltip "Hotkeys & Voice Commands Reference") to open or close the reference popup — or say "help", "reference", "hotkeys", or "voice commands" to toggle it, and "close" + any of those to dismiss it. It is generated directly from the source mappings below, so it can never drift from what the keys and voice grammar actually do:

- [`windows_bridge_hotkeys.ahk`](windows_bridge_hotkeys.ahk) — physical key → dispatch command
- [`fun_time/voice_commands.py`](fun_time/voice_commands.py) — spoken phrase → dispatch command
- [`fun_time/command_reference.py`](fun_time/command_reference.py) — joins both into the popup; `tests/test_command_reference.py` parses the AHK script and cross-checks the voice vocabulary so every real trigger stays represented

This README deliberately does not repeat the key table — open the **?** popup for it. The notes below cover the non-obvious behaviors that the table alone does not explain.

### Active side (side-agnostic satellite voice)

The satellite voice commands can be spoken with or without naming a side. The side word always comes first, so naming one — "portrait lock", "landscape next" — acts on that player as always. Said **bare** — "lock", "unlock", "next", "previous", "weird", "wrong action", "action", "seed" — the command acts on the **active side**: whichever satellite you most recently touched, by voice *or* by keyboard. So if you were just navigating the portrait with `←`/`→`, a plain "lock" locks the portrait; switch to the landscape with `A`/`D` and "lock" now locks the landscape. The active side is remembered (persisted in the bridge's shared state) until the other side is addressed. Bare commands are voice-only — the keys stay side-specific.

Every player says whether it is the one those bare words would reach: the **dot** at the head of its HUD is green on the active player and gray on the others. It is always drawn — an absent dot could not be told from an idle one — so exactly one dot is lit at any moment. Each satellite reads its own off the panel the dispatch loop publishes; Nau is told over `SET_ACTIVE`, appended to its command file so the message cannot displace a queued verb.

### Modes

The main stack runs in one of three modes, each selected by its own hotkey (see the popup): **Nau**, **Genau**, and **Hybrid**. The `\` key is mode-dependent:

- in Nau mode, `\` opens the **library browser** (see below); the chosen video plays in Nau, paired with its funscript when one exists at the mirrored path. Everything keeps playing while you browse — the browser only drops the topmost bands so it is not buried, and never enters OmniPause.
- in Genau and Hybrid modes, `\` offsets Genau playback by a quarter cycle.

The `-`/`=` nudge keys and the `[`/`]` prev/next keys drive Nau in every mode (in Genau mode the paused Nau still navigates in the background). The `'` clip-save key reads the current video/time from Nau's status file in Nau and Hybrid modes.

The Nau-mode voice trigger is spoken as "now now" (the reference displays it as "nau nau" — "nau" itself is not in the recognizer's vocabulary).

### The library browser (Nau mode)

The main library is filed by pipeline stage, several folders deep, and the
same video sits in three of them at different trims and upscales
(`…/larkin/0 unsorted/`, `…/larkin/1 could use work/2_originals_good_trimwise_but_need_upscaling/`,
`…/larkin/3_good_to_go/processed/`). Browsing that tree means knowing how far a
video got through the pipeline before you can find it, which is the librarian's
business and not the viewer's.

So `\` opens Fun Time's own browser instead of a file dialog. It shows one tile
per **video** rather than per file — every rendition of one video collapsed into
a single *handle* — with a still off each, named after the video, and no stage
folders anywhere. Arrow keys move the selection, typing jumps to a title,
and Enter or a double-click plays it in Nau; the window's close button abandons
the browse. The global hotkeys are suspended for its duration so those keys
reach it at all — they consume the press, and the arrows and every letter are
already commands. Escape is the exception: it belongs to OmniPause and stays
live, which is why it is not the way out of the browser.

It is a **Tool window**: no taskbar button, because a browse is something you
open and dismiss rather than a program you leave running. It claims Fun Time's
AppUserModelID before its window exists, so it can never be filed under some
unrelated app. Being a Tool window is also why it has to end its own process:
Qt does not count one towards the last-window quit, so closing the window is
wired to quitting the app — without that a picked video sat in the result file
with the bridge blocked behind a process that had nothing left to do. And it
belongs to the session — quitting Fun Time closes a browse still on screen,
since the dispatch loop that launched it holds it until it ends.

The grid is one you **walk**. It opens on the library's own folders — one tile
per folder under a `nau_library_dirs` source, showing four of its videos laid out
two by two (drawn at random, so a folder is never the same picture twice) and a
count — and opening one shows what is in it: either the folders it was split
into, or its videos. A tile at the head of every folder goes back up, and so
does Backspace.

Down the left is the same folder listed a second way: **its names, A to Z**,
each letter's group under a heading of that letter. The grid is in the library's
own ranking, which is the order to look *through* a folder in and no help when
the title is already in mind — an alphabetical walk across a wrapped grid of
stills is not one. So the sidebar carries no pictures, only names: clicking one
moves the grid's selection to it and scrolls it into view, and Enter or a
double-click there opens it exactly as the tile would. Sub-folders are listed
the same way at the levels that show them; the way back is not, since it is not
something the folder holds. Names that start with a digit or a bracket file
under `#`. The grid keeps the focus, so the arrows and the type-ahead still
drive the tiles, and Backspace goes back up from either half.

The pipeline stages are never steps. Opening the last folder lays out **every**
video under it at once, however many processing folders they are spread across
on disk, because how far a video got through the pipeline is an implementation
detail Fun Time exists to hide — and its other stages are reachable from the
player anyway, as versions. Within a folder it is alphabetical, and the folders
rank by how much of the library each holds, biggest first.

Videos **carved out of a compilation** become a folder of their own beside their
folder's whole videos. Evolver marks an excerpt with a `clip` record (the parent
compilation, the running order in it, the scene it came from), and that record
settles it — so a reel's worth of cuts never sits among the scenes they were
cut from. Where a sidecar has no such record, the folder decides instead: a
source folder that filed its cuts into a folder of their own put nothing else in
there, so a video sitting among them is one of them whatever its sidecar failed
to say. That fallback reaches only where the librarian already drew the line on
disk — a folder whose cuts and whole videos share their sub-folders is separated
by the sidecar alone, and the sub-folders they share are pipeline stages, which
can never stand in for a division of the library.

Where those two sets have been filed into two folders on disk, each
keeping its own copy of the pipeline stages, the tiles are named after those
folders (the cuts folder's name and `full`); where the split is the sidecar's
alone, the cuts take a `· clips` name instead.

The families come from Evolver's metadata sidecar (`version.group`), which is
the authority on "same video, other version" — the filenames alone cannot say
so. A video with no record stands alone as its own handle. Picking one plays its
largest rendition, the same one the main player's own version cycling treats
as canonical, so `V` walks the rest of the family from there. That largest
rendition also decides the section: the odd family that records both an excerpt
and the whole scene sits with the whole videos, because that is what it plays.

A still is grown until it meets one of its tile's edges and no further, so a
tall video and a wide one keep their own proportions rather than both being
squashed into the tile's shape. Stills are cached under `state/hud_thumbnails/` — the same cache the satellites'
HUD maps paint from — and warmed in the background at startup, one per handle,
taken off the *smallest* rendition: a 2.7 GB upscale and the original it came
from make the same picture, and only one of them is cheap to open.

### Loop recording (Nau mode)

Hold `R` to record: a red dot and a growing filmstrip of one thumbnail per recorded second appear on screen. Release to snap the loop to funscript base positions and start looping (amber loop icon). Press `R` again to cancel back to normal playback (play icon). A small corner icon always shows Nau's play/pause/record/loop state. Voice equivalents: "record", "loop", "cancel".

### Sound

One level covers the whole main slot, because it has two audio sinks — Nau's video and the Genau audio companion — and which is audible depends on the mode. The bridge holds the level and the mute; the keys and voice step it, and Nau draws a **volume control** at the right-hand end of the row above its timeline: click the speaker to mute, click or drag the slider to set the level.

A press there posts to the dashboard command file (`audio_set_volume|<0-100>`, `audio_mute`, `audio_unmute`) and the bridge answers on Nau's own channel, so the slider is never the authority — it shows what it asked for straight away, and the answer confirms or corrects it a tick later.

The mute reaches the two sinks differently, which is why `SET_VOLUME` carries two numbers. The audio companion only has to be quiet, so it gets a plain zero. Nau also has to *draw* the level, and a zero cannot say whether you are muted or merely turned all the way down, nor what unmuting should return to — so it gets `SET_VOLUME <level> <muted>` and works the audible loudness out itself. That is why a muted control still shows its fill.

### OmniPause

- `Esc` toggles OmniPause; `Space` enters it.
- While OmniPaused, the global hotkeys are suspended — only `Esc` (toggle OmniPause) and `Ctrl+Alt+Q` (quit) stay active.

### Getting a window out of the way

Minimizing the **dashboard** minimizes the whole room with it (`omniminimize`), and restoring it brings back exactly those windows (`omnirestore`) — one gesture for the session as a whole.

For one player on its own, each satellite's HUD ends its control band with a **minimize bar**. The satellites are borderless — the video fills its slot, so there is no title bar to carry a minimize box — and this is the only affordance that parks one. The player keeps running behind it (its lock, loop and playlist are untouched; the press is about the window, and not even the active side moves to it). Its **taskbar button** is how it comes back, since the panel goes down with the window; a dashboard minimize + restore sweeps it back up too.

### F-Mode

F-Mode is **per player** — the main player, portrait and landscape each have their own — and setting one rebuilds that player's playlist immediately, rather than waiting for the next advance, then sends it `RELOAD_PLAYLIST`. A player that was not named is not rebuilt at all, so narrowing one side never reshuffles the other's queue. What it narrows to differs by player:

- the main playlist (Nau) keeps only videos that have a matching `.funscript` at the mirrored path, where `videos\videos\…` maps to `videos\scripts\scripts\….funscript`
- each satellite plays only items that are in its normal portrait/landscape pool *and* listed in `favs.csv`

Every player carries its own F button in the first row of icons on its own HUD (the satellites' control band, the main console's transport row); the dashboard has no F-mode control. The `F` key and a bare spoken "f mode" still reach all three at once — they turn F-Mode **on** unless every player is already in it, so the whole-room gesture can never leave half the room narrowed. Naming a player narrows just that one: "portrait f mode", "f mode landscape", "main f mode on", "both f mode off" — either word order, and `both` means the two satellites.

`build_all_playlists` writes all three playlist files at startup (each player's F-Mode off, which is what a session with nothing to resume opens in); `apply_fmode` rebuilds the named players after that.

Because the narrowing is invisible in the playlist itself, every HUD says when it is on. Each satellite's status line carries `F-Mode` between the browse order and the act filter (`fun_time/lock_hud.py`), and Nau's mode HUD carries it beside the length mode or compilation — Nau is told over `SET_F_MODE`, since a playlist of scripted videos looks like any other. Each HUD's F button lights green off the same per-player flag, published with the rest of that player's panel.

### Cycle action & cycle seed (satellites)

AI videos under the regen media root carry metadata sidecars (see `regen.media_root` / `metadata_root` in the config) recording the prompts, settings, and seeds they were generated from. Fun Time groups the satellite libraries by that metadata (`fun_time/media_metadata.py`):

- an **action group** is every video generated from the *same source image* — the same subject(s) and setting across different actions (for text-to-video, the same prompt+seed with a different action)
- a **seed family** is every video whose generation config differs *only by seed* — the same scenario rendered from a different seed
- a **loose seed family** is the same scene held only by its prompts and cast/action, with the render knobs (model, resolution, aspect ratio, quality, creativity) freed as well as the seed — a wider net for "the same scene, however it was rendered"

Two command pairs ride on those groups, spoken rather than key-bound ("portrait action", "portrait seed", "landscape action", "landscape seed"):

- **Cycle action** switches the current video to the next action of its group, in a fixed order so repeating it tours every act. The log panel names the action that came up. If the sibling is not in the playlist (grouped builds keep one slot per group — see below), it is swapped in place of the current entry.
- **Cycle seed** jumps to a same-config-different-seed sister, touring the family in seed order — preferring the sisters' existing playlist entries. When no exact sister exists, it widens the net to the loose seed family (same scene, render knobs freed) so a config that differs only in a render setting still surfaces instead of dead-ending on "No other seeds". Every hit narrates itself over the player and in the log panel — **"Next seed"** for an exact same-config sister, **"Similar clip"** for a widened near-match — so you can see at a glance which one fired (and thus watch the widening happen: say "seed" across clips and wait for a "Similar clip").

Unlike prev/next, cycling does **not** release an active lock: it means "show me this differently", so the lock's repeat-one simply carries over to the sibling.

Both groupings are only as good as the acts the sidecars record, so there is a way to say one is wrong: **"wrong action"** (sided or bare, like "weird") strikes `video.action` out of the current clip's sidecar and keeps what it said under `video.wrong_action`. This is the one edit Fun Time makes to a sidecar. A clip with no act reads as one still needing one, so it comes back around in Evolver's Backfill Metadata tool to be named again — and the `wrong_action` key is how that tool tells a clip you rejected from one nobody ever labeled, so it asks about the rejected ones first, whatever source they came from. Nothing about playback changes: the clip is not bad, only mislabeled.

During satellite builds, each action group **collapses to one playlist slot**, so the same subject+scene doesn't recur once per action. Shuffled builds draw that member weighted by the watch stats below; Premiere (`P`, newest-first) instead keeps the group's newest member and orders by recency. Either way it's one entry per group. Videos without a metadata sidecar behave exactly as before.

### Watch stats — videos "breed" by attention

Fun Time watches how you treat each satellite video and adjusts how often it comes up (`fun_time/watch_stats.py`, persisted in `state/watch_stats.json`):

- playing a video through to ~the end counts a **completion**; while locked on repeat, every loop counts again
- pressing next/prev (or cycling action/seed) early in a video counts a **skip**
- **locking** a video is the strongest positive signal

Counts become a playback weight — `2^((completions + 3·locks − skips)/3)`, clamped to between ⅛× and 8× — applied at every shuffled satellite build: weighted shuffle order (loved videos surface early), weighted pick inside collapsed action groups (the acts you finish win the slot), and probabilistic inclusion (a weight-⅛ video sits out ~7 of 8 builds). This is the continuous companion to mark-as-weird: hated videos fade away instead of leaving. Neutral videos are never excluded, and the transitions the system causes itself (unlock's auto-advance, discards) never penalize anything.

Check the current standings any time with the leaderboard (from the project root):

```bash
./.venv/Scripts/python.exe -m fun_time.breeding_report
```

It ranks every tracked clip by weight — "Rising" then "Fading" — with its action, image seed, and prompt pulled from the metadata sidecars. Columns: **WEIGHT** is the shuffle-frequency multiplier; **C** = completions (full watches — one per repeat loop while locked), **L** = locks, **S** = skips, **O** = orientation (P portrait / L landscape). Options: `--top N` (rows per section, default 15), `--all`.

## Favorites CSV behavior

When a satellite VLC is locked, the current media item is added to `favs.csv`.

Specifically:

- locking the portrait VLC writes its current item to `favs.csv`
- locking the landscape VLC writes its current item to `favs.csv`

The CSV contains two columns:

- `local_file`
- `web_url`

These are written as spreadsheet-friendly hyperlink formulas so they are clickable in LibreOffice Calc.

If an item is later discarded, it is removed from `favs.csv`.

## Runtime files in `state/`

### `genau_mode.txt`

Written by the broker (the `../broker` project).

Values:

- `0` = Genau takeover not active (Nau owns playback)
- `1` = Genau takeover active

The audio companion and the Python dispatch loop both read this file as the authoritative source of whether Genau takeover is actually active.

### `genau_cmd.txt`

Written by `fun_time/command_dispatch.py` when Genau control commands are dispatched while Genau is active (Genau/Hybrid mode).

Values:

- `PREV`
- `NEXT`
- `OFFSET_QUARTER_CYCLE`
- `WEIRD`
- `TOGGLE_CLIP_LOCK`
- `CLIP_SECONDS_UP` / `CLIP_SECONDS_DOWN` / `CLIP_SECONDS <seconds>`
- `TOGGLE_CRUISE` / `CRUISE_ON` / `CRUISE_OFF`

`OFFSET_QUARTER_CYCLE` advances Genau playback by one quarter of the current loop.

Cruise control and the clip interval are separate: cruise wanders the stroke's
amplitude, center, speed and waveform, while the interval is how long a clip
holds the screen before Genau moves on — 8–12 seconds unless `CLIP_SECONDS
<seconds>` names a pace. It is spelled for what the number is rather than for
the auto-advance that spends it, because that is the word the reference shows
and the phrase a speaker says ("clip seconds thirty"). The interval keeps
counting while the room is paused, so OmniPause leaves the clip on screen where
the user left it. `TOGGLE_CLIP_LOCK` pins the current clip while the interval
runs on around it; `WEIRD` condemns the clip, moving the file to
`videos/genau/weird/` and taking up its successor.

Genau (the `../genau` project) consumes and clears this file.

### `nau_cmd.txt`

Written by the Python dispatch loop when Nau control commands are dispatched; Nau consumes and clears it.

Commands (the full set `nau/runtime.py` answers to):

- `NEXT` / `PREV`
- `SEEK_FWD` / `SEEK_BACK`
- `SPEED_UP` / `SPEED_DOWN` / `SET_SPEED min|max|<rate>`
- `SET_VOLUME <0-100> [muted]` — the level to *show* plus whether it is muted; Nau derives the audible loudness (see "Sound")
- `RECORD_DOWN` / `RECORD_UP` / `RECORD_TAP`
- `LOOP_CANCEL`
- `CYCLE_VERSION`
- `PLAY_FILE video[TAB]funscript`
- `RELOAD_PLAYLIST`
- `TOGGLE_LENGTH_MODE` / `SET_LENGTH_MODE mixed|shorts|full`
- `PLAY_COMPILATION` / `END_COMPILATION` / `PLAY_FULL_VID` / `PLAY_CLIP_JUMP`
- `JUMP_TO_FUNSCRIPT` / `NEXT_FUNSCRIPTED` — funscript navigation: seek past this video's quiet stretch to where its scripting starts up again, or leave for the next scripted video in the playlist, landing where its action begins. Nau alone can answer either, holding both the playlist's funscript column and the parsed script of what is playing
- `SET_TCODE_ENABLED 0|1`
- `SET_HYBRID 0|1` / `SET_F_MODE 0|1` / `SET_ACTIVE 0|1` — state only the orchestrator holds and Nau cannot work out for itself; all three drive what its HUD shows
- `DISPLAY_ON` / `DISPLAY_OFF` — whether Nau owns the main player's rect, which is not whether it is playing: the idle main-slot player is minimized rather than closed (it keeps its taskbar button), so in Genau mode Nau blanks instead of sitting on the frame it was paused on. The same pair Genau gets, for the same reason
- `QUIT`

### `nau_paused.txt`

Flag file — Nau's pause channel. Mode switches and OmniPause write it; Nau polls it every tick.

### `nau_status.txt`

Written by Nau: the current `video`, `position_ms`, `duration_ms`, `has_funscript`, `state`, and `paused`. Read by `clipper_save` (for the current video/time outside Hybrid mode) and by the dashboard.

### `watch_stats.json`

Per-video watch counts (`completions` / `skips` / `locks`) keyed by normalized path — the input to the frequency weighting described under "Watch stats". Written by the dispatch loop's ~1 Hz satellite sampler and by lock commands; entries whose file vanished (e.g. marked weird) are pruned on the next write. Delete the file to reset all weights to neutral.

### `library_browser_pick.txt`

The video the library browser picked, written as it closes and consumed by the
dispatch loop, which turns it into Nau's `PLAY_FILE`. The browser is a separate
process (the bridge has no Qt event loop), so this is how the pick gets back.
Cleared before every browse, so abandoning one never replays the last pick.

### `nau_playlist.tsv`

One video per line, with a TAB plus the funscript path when one exists. Written by `build_all_playlists` at startup and by `apply_fmode` whenever the main player's F-mode changes (which also sends Nau `RELOAD_PLAYLIST` and `SET_F_MODE`, on one write — the command file is overwritten, not appended).

### `event_log.jsonl`

Every line any `fun_time` logger emits during a session, one JSON object per line:

```json
{"ts": 1752000000.5, "level": 25, "source": "portrait", "msg": "No other seeds"}
```

`source` is the window the line is about — `main`, `portrait`, `landscape`, `dash`, or `system` for the session at large. `level` is a standard `logging` level plus **`NOTICE` (25)**, the tier for messages meant for whoever is watching the screen ("Clip saved", "Similar clip"). These used to flash as AutoHotkey tooltips under the mouse pointer; now they flash over the player they name (top-center) and land here in the stream.

The file is truncated when a session starts, so it always holds exactly the current run. The log panel tails it — and so can you, or an agent debugging a session: pause, describe the symptom, and the answer is in this one file.

### Log files

The Python entry points also write rotating logs in `state/`:

- `state/orchestrator.log`
- `state/windows_bridge.log`
- `state/genau_audio.log`

The broker and Genau write their own logs (e.g. `broker.log`, `genau_listener.log`, `genau_crash.log`) from the `../broker` and `../genau` projects.

## Notes on design

### Why the broker exists

Windows serial ports are effectively single-owner, so exactly one process can own `COM4`.

With the broker:

- the broker alone opens real `COM4`
- Nau and Genau drive the OSR2 by sending T-Code to the broker over UDP; the broker forwards it and suppresses serial input while UDP flows
- Genau gets mode/timing info over localhost instead of serial

(Historically, MultiFunPlayer sat on the other side of a `com0com` virtual serial pair from the broker; MFP and that pair are gone now that UDP T-Code is the only control path.)

### Why Genau and Nau use local files for commands/mode

For this setup, file-based signaling turned out to be a reliable and simple way to let:

- Python dispatch loop
- broker
- Genau
- Nau

coordinate mode, playback, and clip-switch commands without depending on focused windows.

## Troubleshooting

### Nothing happens from the taskbar launcher

First run a config check:

```powershell
python -m fun_time.orchestrator --check
```

If that passes, run manually from PowerShell:

```powershell
python -m fun_time.orchestrator
```

Alternative compatibility run via `main.sh`:

```bash
cd /path/to/fun_time && bash ./main.sh
```

Also verify the shortcut’s **Start in** points to the project folder.

If startup still fails, inspect:

- `state/orchestrator.log`
- `state/windows_bridge.log`

### The OSR2 does not respond to Nau

Check:

- broker is running
- scheduled task `FunTime Genau Broker` is present and running (`Get-ScheduledTask -TaskName "FunTime Genau Broker"`)
- OSR2 is still on `COM4`
- the current video actually has a funscript (`state/nau_status.txt` shows `has_funscript`) — videos without one play with no OSR2 output by design
- the broker's log (in `../broker`) for serial/COM-port errors

### Genau never appears

Check:

- broker is running
- OSR2 is actually entering auto/free mode
- `state/genau_mode.txt` changes to `1`
- the broker's log (in `../broker`) for serial parsing / mode transitions
- Genau's log (in `../genau`) for UI/runtime errors

### Broker will not start

The broker is the `../broker` project — check its logs and config there. Also confirm that current serial ports still include the real OSR2 on `COM4`.

### `M` and `.` do not switch Genau clips

Check:

- `state/genau_mode.txt` is `1`
- `state/genau_cmd.txt` is being written
- clip files exist in the configured Genau clips folder (`paths.clips_dir`)
- `state/windows_bridge.log` shows the hotkey write
- Genau's log (in `../genau`) shows command-file consumption errors

### A clip stutters badly

The clip is probably too large in pixel dimensions. Make a smaller version and use that instead.

## Git / local-only files

These should generally be ignored:

- `state/`
- `archive/`
- `*.lnk`
- `favs.csv`

## Current source of truth

These are the files that define the working system:

- `fun_time_config.json`
- `main.sh`
- `launch.vbs`
- `launch_branch.vbs`
- `windows_bridge_hotkeys.ahk`
- `fun_time/config.py`
- `fun_time/orchestrator.py`
- `fun_time/command_dispatch.py`
- `fun_time/dashboard_app.py`
- `fun_time/audio_companion_app.py`

The broker, Genau/Nau, and Clipper are separate projects: `../broker`, `../genau`, `../clipper`.

## Refactors completed

Completed from the earlier cleanup list:

- orchestration now lives in `fun_time/orchestrator.py`, with `main.sh` kept as a thin wrapper
- config is centralized in `fun_time_config.json`
- Genau, the broker, and Clipper have been extracted to their own sibling projects (`../genau`, `../broker`, `../clipper`)
- window/layout constants are configurable through `layout`
- runtime logging and diagnostics are written to `state/*.log`

## Developing

Before doing repo work, consult [CLAUDE.md](CLAUDE.md) for the required preflight and the canonical test commands.

### Running the tests

Tests live in `tests/` and use [pytest](https://docs.pytest.org/).

Install pytest into the project venv if it isn't there yet:

```bash
.venv/Scripts/pip.exe install pytest
```

Run everything:

```bash
bash test.sh
```

Windows-safe direct equivalent:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Stop on the first failure and show a short traceback:

```bash
bash test.sh -x --tb=short
```

Run only tests whose name matches a keyword (e.g. just the clipper tests):

```bash
bash test.sh -k clipper
```

`test.sh` is a thin wrapper around `.venv/Scripts/python.exe -m pytest`. Any extra arguments are forwarded to pytest directly.

### Test layout

| File | What it covers |
|---|---|
| `tests/test_config.py` | `fun_time.config` — loading, validation, derived properties |
| `tests/test_logging_utils.py` | `fun_time.logging_utils` — handler setup, exception hooks |
| `tests/test_orchestrator.py` | `fun_time.orchestrator` — arg parsing, path checks, windows-bridge arg building |
