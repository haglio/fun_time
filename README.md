# Fun Time

Fun Time is a Windows desktop setup that launches and coordinates:

- the Main Funestra, which plays the main library's funscript videos (this repo's `main_funestra` package, launched as `python -m main_funestra`)
- two Satellite Funestras, portrait and landscape (this repo's own mpv-based `satellite` package, launched as `python -m satellite`)
- Genau, a visualizer for OSR2 auto mode: short looping videos, its flicks, scrubbed to wherever the OSR2 is, on the Main Funestra (`main_funestra/genau.py`; the layout of its flicks folder is `../genau`'s contract). The apps show and hear the flicks under the word the content overlay's `genau_flick_words` gives them
- a Genau audio companion
- a minimal AutoHotkey hotkey shell (window placement and command dispatch run in Python)

It uses a serial broker for the OSR2 — the separate `../broker` project — that is intended to run continuously in the background.

The main stack runs in one of two modes (startup mode is **video**):

- in **Kino mode**, the Main Funestra owns the main slot and plays the whole main library, with Genau's HUD as a see-through layer over it. The OSR2 goes to the video's funscript while it has action (the Main Funestra sends funscript-derived T-Code over UDP to the broker), and to the **Robot Hand** — the family's own motion generator, run from Genau's process — through the quiet stretches and for videos without a script
- in **Genau mode** (OSR2 auto/free mode), Genau flicks own the main slot and the Robot Hand drives the OSR2 outright

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

- `state/broker_mode.txt`
- `state/genau_paused.txt`
- `state/genau_cmd.txt`
- `state/main_player_cmd.txt`
- `state/main_player_paused.txt`
- `state/main_player_status.txt`
- `state/main_player_playlist.tsv`
- `state/audio_paused.txt`
- `state/*.log`

Local runtime data:

- `favs.csv` — favorites CSV written whenever something is locked
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
- the audio companion's and the headset's network endpoints
- monitor/layout ratios used by the Python window layout

Every section `fun_time/config.py` reads. The first three are required and the rest optional:

- `paths`
- `layout`
- `audio_companion`
- `random_favs_browser`
- `voice_control`
- `regen`
- `vr`

For the satellite AI libraries, Fun Time can now read either a single folder or multiple folders:

- `paths.portrait_dir` or `paths.portrait_dirs`
- `paths.landscape_dir` or `paths.landscape_dirs`

If the list form is used, the portrait or landscape satellite gets all listed folders joined into one rotating source set.

The Main Funestra's video library folders are configured with `paths.main_player_library_dirs` (a list of one or more folders):

Example:

```json
{
  "paths": {
    "main_player_library_dirs": [
      "C:/videos/set_a",
      "C:/videos/set_b"
    ]
  }
}
```

Genau's playback settings — `shuffle_on_load` among them — are read out of Genau's own config file, the one `paths.genau_config_path` names, and never out of this one. Where Genau's folder and the folder of its sounds are is not in this config either: `genau_dir` and `genau_audio_dir` in the settings all of Haglio shares, `~/.haglio/settings.json`, say so for every app.

The layout values that used to be hard-coded in AutoHotkey now live under `layout`.

Monitor naming under `layout` now uses:

- `primary_monitor` — the monitor that shows the landscape satellite, the dashboard, and the Random Favs Browser
- `secondary_monitor` — the monitor that shows the portrait satellite and the shared main-player slot (the Main Funestra and Genau use the same rect)

## High-level architecture

Serial / mode control:

- the real OSR2 is on its USB serial adapter, which the **broker** — the separate `../broker` project — finds on whichever USB socket it is plugged into, by the adapter's USB identity rather than a COM name; the broker is the only process that talks to it. It forwards UDP T-Code to the OSR2 unconditionally and suppresses serial input while UDP flows, watches the OSR2 for free-mode transitions, and publishes mode/timing state over localhost and `state/broker_mode.txt`.
- **the Main Funestra** — this repo's `main_funestra` package — never opens the OSR2's port. It drives the OSR2 itself by sending funscript-derived T-Code to the broker over UDP (the same port Genau uses), reads commands from `state/main_player_cmd.txt`, and publishes playback status to `state/main_player_status.txt`.
- **Genau** — run on the Main Funestra (`main_funestra/genau.py`) — never opens the OSR2's port either. It follows the broker-fed state, is the display in Genau mode and the see-through HUD layer over the Main Funestra in Kino mode, and reads clip and Robot Hand commands from `state/genau_cmd.txt`.

See those projects for the serial parsing, COM-port recovery, and playback internals.

### The log panel

The main monitor's left column stacks the **Dashboard** across its top and the **Random Favs Browser** filling the rest below. The Dashboard spans the full column width: a control bar across the top and the **log panel** embedded under it.

The log panel is a widget inside the dashboard window — one window, not two — so it rides the dashboard's topmost band, minimize/restore and close. It tails `state/event_log.jsonl` and shows the **stream** of everything the session logs, filtered by a verbosity dial (`DEBUG`/`INFO`/`NOTICE`/`WARNING`/`ERROR`, default `NOTICE`) and by per-window toggles across one compact row. Both settings persist in `state/log_panel_state.ini`.

The brief **notices** — "Clip saved", "No other seeds", "Next seed", "Similar clip" — flash over the top-center of the Funestra they concern (a portrait notice over the portrait satellite, a main-player notice over the Main Funestra/Genau display) and then fade. They also land in the stream, colored by level — red for an error, yellow for a warning such as a dead end, green for the favorites and the funscripts, white for everything else — so the panel is where you scroll back through them. The flash always fires regardless of the verbosity dial, which governs only the stream. Long lines in the stream **word-wrap** rather than being cut off, so the tail of a message (a video name, a phrase heard) is readable.

Every recognized voice command flashes a **white confirmation** — the phrase it matched — over the Funestra it addresses, so you can see what was heard. While OmniPause is on, a command that is heard but ignored — anything but resume, quit, retracting the OSR2, enter VR, exit VR, recenter and VR reset — flashes **"ignored during OmniPause: ‹the phrase›"** in yellow instead, so a paused room says why it did nothing. While nobody is wearing the headset, a command other than quit flashes **"ignored while not wearing headset: ‹the phrase›"** the same way. A command the room's mode has nothing for says which mode ignored it, in yellow: Origenerator's words in Kino mode ("ignored in Kino mode: landscape experiments"), Genau's with a video on the Main Funestra, the Main Funestra's own in Genau mode ("ignored in Genau mode: compilation"), the headset's on the monitors ("ignored outside VR: recenter"), and a satellite's said bare while the Main Funestra was the last one addressed ("ignored on the Main Funestra: weird"). A command that hits a dead end ("No other seeds", "No action metadata") flashes **yellow** instead. And when the recognizer clearly hears speech that matches no command, it flashes **"unrecognized voice command: ‹what it heard›"** in yellow — over the Funestra the phrase named, if it named one ("landscape ‹something garbled›" reports on landscape, not the Main Funestra) — a second, unrestricted recognizer runs alongside the grammar one purely to transcribe that, so an out-of-grammar phrase surfaces as text instead of vanishing. What it heard and did not act on shows its words in the flash and in the log panel, in the headset's too, for as long as the session runs; the stream saved to `state/event_log.jsonl` lists only what kind of miss it was and how many words it heard ("unrecognized voice command (4 words)"). Each "Figuring out what you said..." ends in exactly one such line or command line, and a miss it never said it was figuring out says nothing.

Two listeners hear every command (the family's shared listener, `../voice_core`). The first proposes what was said from the whole phrase list; the second has to read the same command in the same audio before anything happens, which takes about half a second and is what keeps a cough or ordinary talk from firing a command — on recordings of ordinary talk it took false commands from about 9 in 100 utterances to about 1. It listens only to the moment the first heard the command in, not to the noise around it, and listens a second time, more closely, before it disagrees. Genau is a German word its English cannot place, so before it disagrees about Genau it listens once more in German, which takes about a second. It starts loading as a session begins to launch, so it is ready before the room appears. **"stop"** alone acts on the first listener's word, because relief must not wait. A command the first listener was not confident enough of, or the second did not agree to, flashes **"not sure enough of: ‹the phrase› (press Enter to accept)"** in yellow and does nothing until you press **Enter**, which carries out the last such command as if it had been heard for sure. Turning voice off drops it, and with nothing waiting Enter flashes "Nothing to accept". `"confirm_commands": false` under `voice_control` in the config lets every command act on the first listener's word; the second is then only asked about what the first could not settle.

## Requirements

### Windows apps

- AutoHotkey v2

### Python / tools

- Python (currently launched via Miniconda `pythonw.exe`)
- Python dependencies are declared in `pyproject.toml` — notably PyQt6 (dashboard), pygame-ce (audio companion), voice_core (the family's listener, which brings vosk + sounddevice; the `voice` extra adds faster-whisper for the second listener), and Pillow / numpy / opencv-python.
- Kino and Genau run on the Main Funestra, which Fun Time starts as `python -m main_funestra` from this project's venv (`paths.python_exe`), like the Satellite Funestras. It reads its library folders and device port from this project's config, and Genau's engine settings from Genau's config file (`paths.genau_config_path`). The `../genau` repo ships no code: it holds Genau's settings and the flicks-folder layout it promises (`genau_contract.json`).

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

Which checkouts of the shared code (`../funestra`, `../app_support`,
`../shared_ui`, `../voice_core`) a session runs is a per-session fact:
`paths.sibling_checkouts` puts directories in front of this project's venv
install for every program the session starts, so a branch of *funestra* can be
judged the same way. That is per session and must be said per session — written
into the machine's `fun_time_config.json` it reaches the ordinary `launch.vbs`
session and every other agent's branch session too, each then silently running
someone's unlanded branch of another repo. So a worktree says it for itself, in
`state/sibling_checkouts.txt`: one absolute path per line, `#` comments and blank
lines ignored. Present, it replaces the machine's value outright — an **empty**
file is how a branch that changes none of them says "the venv's installs", which
is what you want when the machine is pinned to a worktree that predates what has
landed. Absent, the machine's value rides through as before. Until 2026-10-16
both were named for Genau (`paths.genau_project_dirs`,
`state/genau_project_dirs.txt`), and a session still reads them under those
names when the new ones are absent.

### Genaumacher way

Genaumacher has been extracted to its own project at `../genaumacher`. See that project for usage details.

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

The satellite voice commands can be spoken with or without naming a side. The side word always comes first, so naming one — "portrait lock", "landscape next" — acts on that Funestra as always. Said **bare** — "lock", "unlock", "next", "previous", "skip", "back", "weird", "wrong action", "action", "seed" — the command acts on the **active side**: whichever satellite you most recently touched, by voice *or* by keyboard. So if you were just navigating the portrait with `←`/`→`, a plain "lock" locks the portrait; switch to the landscape with `A`/`D` and "lock" now locks the landscape. The active side is remembered (persisted in the bridge's shared state) until the other side is addressed. Bare commands are voice-only — the keys stay side-specific.

Every Funestra says whether it is the one those bare words would reach: the **dot** at the head of its HUD is green on the active Funestra and gray on the others. It is always drawn — an absent dot could not be told from an idle one — so exactly one dot is lit at any moment. Each satellite reads its own off the panel the dispatch loop publishes; the Main Funestra is told over `SET_ACTIVE`, appended to its command file so the message cannot displace a queued verb.

### Playback speed

Every Funestra has its own playback speed: the Main Funestra's row on its console, and the same **Playback speed** row on each satellite's HUD — slower, the rate, faster. When the Main Funestra's speed changes, both satellites are set to the same speed; a satellite you set on its own keeps its speed until the Main Funestra's changes again. Spoken, a playback speed phrase — "playback speed up", "max speed", "half speed", "reset speed", "speed one point five ex" — reaches the Funestra you last addressed, and "main", "portrait", "landscape" or "both" before or after it aims it at that Funestra. The bare "speed up" and "slow down" still follow whichever engine is driving the OSR2. A satellite answers `SPEED_UP`, `SPEED_DOWN` and `SET_SPEED min|max|<rate>` on its command file, the same three the Main Funestra does.

### Modes

The main stack runs in one of two modes, each selected by its own hotkey (see the popup): **Kino** (`H`) and **Genau** (`G`). `N` opens the **library browser** (see below); the chosen video plays in the Main Funestra, paired with its funscript when one exists at the mirrored path. Everything keeps playing while you browse — the browser only drops the topmost bands so it is not buried, and never enters OmniPause.

The `-`/`=` nudge keys and the `[`/`]` prev/next keys drive the Main Funestra in every mode (in Genau mode the paused Main Funestra still navigates in the background). A ten-second step has nothing to step through in a video of 10 seconds or shorter, so there every Funestra's HUD draws its back and forward 10s buttons faded; and wherever a step has less than 10 seconds to go (back in a video's first 10 seconds, forward in its last 10), a key, a press of either button or a spoken "skip" or "back" does nothing but say so over that Funestra. The `;` clip-save key reads the current video/time from the Main Funestra's status file in Kino mode.

Spoken, "kino mode" puts both sides in Kino mode at once; "main kino mode" and "satellite kino mode" do one side, and "genau mode" or "origenerator mode" puts a side back. Genau is heard said either way, the German word or "go now"; "origenerator" is in no dictionary, so the recognizer listens for "aura generator mode".

Each Satellite Funestra has a mode of its own, orthogonal to the two above: **Kino mode** (it plays its own videos) and **Origenerator mode** (it plays Origenerator's slideshows), switched by the Kino / Origenerator pair at the head of its HUD. What holds the Random Favs Browser's place is a switch of its own too: the strip of two buttons under the dashboard's log, **Random Favs Browser** and **Origenerator**. With `paths.origenerator_dir` configured, the session launches that checkout of [Origenerator](../origenerator) at startup with its `--fun-time` flag: its main window sits over the RFB's rect while the strip says Origenerator (parked under the browser otherwise), and the slideshows it opens play on the portrait or landscape Satellite Funestra by each subject's orientation; a show started for a Funestra still in Kino mode switches that Funestra to Origenerator mode and plays there. Switching a Funestra to Origenerator mode opens it on the whole Origenerator library of its own shape, shuffled — the same base state it holds in Kino mode, and what its reset button goes back to. That Funestra then wears the app's own map and buttons under the session's mode row, and everything said to it — its buttons, the satellite transport hotkeys (arrows, `A`/`D`/`W`/`S`), its spoken commands — reaches the hosted app for as long as it is in that mode; only minimize, the playback speed and its own mode pair stay the Funestra's. Back in Kino mode it plays its own list again, on the clip it was playing when it left. `X` switches both Satellite Funestras at once — into Origenerator mode unless both already are, and back to Kino mode if they are — and so do "origenerator mode" and "satellite kino mode" said aloud; none of the three moves what holds the browser's place. A session hosting no Origenerator draws neither the mode pair nor the strip. The OSR2 stays entirely with the main stack: a hosted Origenerator builds none of its own OSR2 surface, and a standalone one that the session never took over stands off the device too — the session leaves its pid in `state/fun_time_session.txt` for as long as it runs, in that checkout and in the primary checkout a worktree belongs to, and a window that reads a live one there lets go of the OSR2 and keeps its switch off. A preview of an Origenerator branch reads the primary's as well as its own, so one open beside Fun Time lets go too. Origenerator keeps generating throughout; it is the same live install, just wearing the session's geometry. Fun Time's hotkeys stay live while Origenerator's window has the keyboard, and clicking into one of its text fields enters OmniPause, which suspends them, so what you type goes into the field.

### The library browser

The main library is filed by pipeline stage, several folders deep, and the
same video sits in three of them at different trims and upscales
(`…/<folder>/0 unsorted/`, `…/<folder>/1 could use work/2_originals_good_trimwise_but_need_upscaling/`,
`…/<folder>/3_good_to_go/processed/`). Browsing that tree means knowing how far a
video got through the pipeline before you can find it, which is the librarian's
business and not the viewer's.

So `N` opens Fun Time's own browser instead of a file dialog. It shows one tile
per **video** rather than per file — every rendition of one video collapsed into
a single *handle* — with a still off each, named after the video, and no stage
folders anywhere. Arrow keys move the selection, typing jumps to a title,
and Enter or a double-click plays it in the Main Funestra; the window's close button abandons
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
with the bridge blocked by a process that had nothing left to do. And it
belongs to the session — quitting Fun Time closes a browse still on screen,
since the dispatch loop that launched it holds it until it ends.

In the headset the browser is the desktop's own. Nothing in the headset can see
a window on the monitors, so a VR session runs the same browser with no window
at all and hangs what it draws in front of you as a panel. `N`, "browse" and
the console's browse button open it, and the same again puts it away. It is the
window described below, header, letters and tiles alike, with three differences
a pointer needs: one trigger press opens a tile, the stick scrolls whichever
list the pointer is over, and a close button sits at the end of the header,
since no title bar is drawn around it. A headset session has two kinds of
video, so the browse opens on two folders: `VR`, the headset's own library
(`vr.library_dirs`), and `2D`, the folders the desktop walks; a session with
videos of only one kind opens straight on that kind's folders. A video picked
plays on the Main Funestra and puts the browse away. It opens on the folder of
the video that is playing, with that video chosen, and no hotkey is suspended
while it is up. The browser starts, and reads the library, when the headset's
Funestra does, so a browse opened in the first moments of a session says it is
still reading.

The grid is one you **walk**. It opens on the library's own folders — one tile
per folder under a `main_player_library_dirs` source, showing four of its videos laid out
two by two (drawn at random, so a folder is never the same picture twice) and a
count — and opening one shows what is in it: either the folders it was split
into, or its videos. A tile at the head of every folder goes back up, and so
does Backspace. The folder you are in is named across the top of the window,
after **Library** and every folder above it, each of which is a link back to
that folder.

Down the left is the same folder listed a second way: **its letters, A to Z**,
every one of them always, after `#` for names that start with a digit or a
bracket. The grid is in the library's own ranking, which is the order to look
*through* a folder in and no help when the title is already in mind — an
alphabetical walk across a wrapped grid of stills is not one. A letter with
names under it carries ▸; clicking it moves the grid to its first name, opens
its names beneath it with ▾, and closes whichever letter was open before, and
clicking an open letter closes it again. A letter with nothing under it is
grayed out and does nothing. A browse opens with the letter of the video playing
already open. Clicking a name moves the grid's selection to it and scrolls it
into view, and Enter or a double-click there opens it exactly as the tile would.
Sub-folders are listed the same way at the levels that show them; the way back
is not, since it is not something the folder holds. The grid keeps the focus, so
the arrows and the type-ahead still drive the tiles, and Backspace goes back up
from either half.

The pipeline stages are never steps. Opening the last folder lays out **every**
video under it at once, however many processing folders they are spread across
on disk, because how far a video got through the pipeline is an implementation
detail Fun Time exists to hide — and its other stages are reachable from the
Funestra anyway, as versions. Within a folder it is alphabetical, and the folders
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
keeping its own copy of the pipeline stages, the browse names them after those
folders; where the split is the sidecar's alone, they are `full` and `clips`.

The families come from Evolver's metadata sidecar (`version.group`), which is
the authority on "same video, other version" — the filenames alone cannot say
so. A video with no record stands alone as its own handle. Picking one plays its
largest rendition, the same one the Main Funestra's own version cycling treats
as canonical, so `V` walks the rest of the family from there. That largest
rendition also decides the section: the odd family that records both an excerpt
and the whole scene sits with the whole videos, because that is what it plays.

A still is grown until it meets one of its tile's edges and no further, so a
tall video and a wide one keep their own proportions rather than both being
squashed into the tile's shape. Stills are cached under `state/hud_thumbnails/` — the same cache the satellites'
HUD maps paint from — and warmed in the background at startup, one per handle,
taken off the *smallest* rendition: a 2.7 GB upscale and the original it came
from make the same picture, and only one of them is cheap to open.

In Genau mode the same key, word and button browse **Genau's flicks** instead,
under a header that starts at **Genau**: one tile per flick, named for its file,
over Genau's own window, opening on the flick Genau has up, the way the video
browse opens on the video playing. Genau's flicks folder (`paths.flicks_dir`) is
laid out as the library's videos are: a `2D` folder split into `AI` (the flicks
Origenerator makes) and `non_AI` (the flicks Genaumacher cuts), and a `VR` folder.
Either room's browse holds `VR` and `2D`, the way the headset's video browse
does. The desktop's Genau cycles through the 2D flicks alone, and a VR flick
picked on the desktop plays there as it is, both eyes side by side. A flick
picked goes up in Genau at once and Genau carries on from it in the order it
was browsing; one that is not in that order, a VR flick on the desktop or one
that arrived after Genau last read its folders, is put in after the flick that
was up. The flicks are read
afresh at every browse, so a flick Evolver delivered a minute ago is there, and
their stills are warmed at startup with the library's.

### Loop recording (Kino mode)

Hold `R` to record: a red dot and a growing filmstrip of one thumbnail per recorded second appear on screen. Release to snap the loop to funscript base positions and start looping (amber loop icon). Press `R` again to cancel back to normal playback (play icon). A small corner icon always shows the Main Funestra's play/pause/record/loop state. Voice equivalents: "record", "loop", "cancel".

### Sound

One level covers the whole main slot, because it has two audio sinks — the Main Funestra's video and the Genau audio companion — and which is audible depends on the mode. The bridge holds the level and the mute; the keys and voice step it, and the Main Funestra draws a **volume control** at the right-hand end of the row above its timeline: click the speaker to mute, click or drag the slider to set the level.

A press there posts to the dashboard command file (`audio_set_volume|<0-100>`, `audio_mute`, `audio_unmute`) and the bridge answers on the Main Funestra's own channel, so the slider is never the authority — it shows what it asked for straight away, and the answer confirms or corrects it a tick later.

The mute reaches the two sinks differently, which is why `SET_VOLUME` carries two numbers. The audio companion only has to be quiet, so it gets a plain zero. The Main Funestra also has to *draw* the level, and a zero cannot say whether you are muted or merely turned all the way down, nor what unmuting should return to — so it gets `SET_VOLUME <level> <muted>` and works the audible loudness out itself. That is why a muted control still shows its fill.

### Max intensity

The OSR2 line carries a **Max intensity** slider, a blue bar with its number beside it: how hard the OSR2 may work, whoever is driving it. All the way up puts no limit on it. Lower, it pushes the Robot Hand's Amp and Speed down together, and its Center toward home, far enough that the motion fits, and they stay where it pushed them when it goes back up. Amp and Speed then stop at the most it allows, one against the other: the stretch of each bar they can no longer reach is etched in red, and a drag stops where the etching starts. A funscript's motion gets shallower toward home instead, the way it already does when a video plays faster than the OSR2 can follow. All the way down, nothing moves. The slider is dimmed while the OSR2 runs its own auto mode, which nothing sent from here reaches.

A press posts `max_intensity_<0-100>` to the dashboard command file; the bridge keeps the level with the rest of the session, tells Genau and the Main Funestra `SET_MAX_INTENSITY <0-100>`, and seeds it again when the session comes back.

### OmniPause

- `Esc` toggles OmniPause; `Space` enters it.
- Clicking into a text field in the Random Favs Browser or in Origenerator enters it too, so what you type goes into the field instead of to the hotkeys. A click anywhere else in those windows leaves the room playing.
- While OmniPaused, the global hotkeys are suspended — only `Esc` (toggle OmniPause), `Shift+Esc` (Relief Omnipause) and `Ctrl+Alt+Q` (quit) stay active.
- Entering it parks the OSR2, and in Genau mode Genau's picture goes home with the device: it holds for the second the broker waits, then glides to the flick's parked end as the device settles there. `Shift+Esc` is OmniPause and Retract together: the OSR2 goes to the far end, the picture with it, the OSR2 line says Retracted with the OSR2 switch off, and it stays retracted after the room resumes until you switch it on again. Park holds the picture with the device the same way, and when the Robot Hand takes the device back the picture eases onto its motion as the device does. With the OSR2 switched off there is no device for the picture to follow, so none of these moves it: OmniPause stops it where it is, and a park leaves it playing.

### With the headset off

- In a VR session, while nobody is wearing the headset, the global hotkeys are suspended and `Esc` and `Shift+Esc` go off with them: only `Ctrl+Alt+Q` (quit) stays active, so whatever you type reaches the window you are typing in. Spoken commands do nothing either, but "quit". Putting the headset back on lets them all go again.

### The satellites' scrubber and volume chip

Each satellite draws the same two controls the Main Funestra does along the lower edge of its video — the scrubber and the volume chip — and, like the Main Funestra's, both answer a press. Clicking anywhere on the bar seeks the clip on screen, which is what a longer video in a satellite needed and had no way to ask for; the playlist does not move, so the prefetched next clip is still there when it plays out. On the chip, the speaker mutes and unmutes and the slider sets the level, dragging as well as clicking.

A satellite in a fresh room **opens muted** whatever its clips carry — the room's sound is the Main Funestra's, and two more voices under it would be noise — so the speaker is how you hear one, and the fill under the mute is the level unmuting comes back to. The level is that Funestra's own: it goes straight to its mpv rather than through Fun Time, which arbitrates only the main slot's two sinks. Fun Time keeps what each satellite reports of its chip with the room's other settings, so a reopened room hands each satellite back the level and mute it was left at. The hidden-desktop integration runs stay permanently silent (`FUN_TIME_MUTE_AUDIO`), their chips read-only indicators, so an unattended run beside a live session can never be heard.

A press on the video itself still does nothing: a satellite's paused state belongs to the flag file under OmniPause, so a pause toggled in the window would be gone by the next frame.

### Getting a window out of the way

Minimizing the **dashboard** minimizes the whole room with it (`omniminimize`), and restoring it brings back exactly those windows (`omnirestore`) — one gesture for the session as a whole.

For one Funestra on its own, every Funestra's HUD carries a **minimize bar**: last in each satellite's control band, and beside the mode buttons on the main console (the row that is the same in every mode, so it does not move as you flip). Every one of these windows is borderless — the video fills its slot, so none has a title bar to carry a minimize button — and this is the only affordance that parks one. The main console's button parks the Main Funestra's window, which Kino and Genau both run on. In the headset there is no minimize bar at all: its Funestras are screens in the scene rather than windows, so there is nothing for one to park.

The Funestra keeps running under it — its lock, loop and playlist are untouched, and the press does not even move the active side, since a Funestra you have just put away should not be the one a bare "lock" reaches. Three things bring it back: its own **taskbar button** (the panel went down with the window, so there is nothing left to press), a dashboard minimize + restore, and **leaving OmniPause** — resuming is the room coming back, so every window a minimize button parked returns to its slot, in its band, before the re-stack and the focus land.

### Closing one window closes the session

There is no window of a session you can close on its own. Every Funestra answers the close the dashboard's own window has always answered — by asking Fun Time to quit — so Alt+F4, the taskbar's Close and the system menu all end the session as a whole, under the closing cover, exactly like the dashboard's Quit button, `Ctrl+Alt+Q` or a spoken "quit".

However a session ends — any of those, or a crossing into or out of the headset — the room stops before the closing cover goes up: every Funestra pauses, the Robot Hand stops, and the OSR2 parks, the way entering OmniPause parks it, or stays at the far end if Relief Omnipause or Retract had sent it there. From then on nothing said or pressed reaches the room; Esc on the cover still calls the quit off.

The alternative was worse than it sounds: a Funestra that closed itself left the sequencer's layout with a gap nothing refills, and it was reachable by accident — a Mac keyboard's Cmd+Q arrives as Alt+F4, which took the Funestras out one press at a time while the dashboard and the audio companion carried on. Launched by hand, outside a session, a satellite or the Main Funestra still closes on its own close, because then that is the whole application; Genau runs only inside a session. See `funestra.session_quit`.

### F-Mode

F-Mode is **per Funestra** — the Main Funestra, portrait and landscape each have their own — and setting one rebuilds that Funestra's playlist immediately, rather than waiting for the next advance, then sends it `RELOAD_PLAYLIST`. A Funestra that was not named is not rebuilt at all, so narrowing one side never reshuffles the other's queue. What it narrows to differs by Funestra:

- the main playlist (the Main Funestra) keeps only videos that have a matching `.funscript` at the mirrored path, where `videos\videos\…` maps to `videos\scripts\scripts\….funscript`
- each satellite plays only items that are in its normal portrait/landscape pool *and* listed in `favs.csv`
- Genau, which has the main slot in Genau mode, keeps only the flicks listed in `favs.csv` — a lock on Genau puts its flick there, as a satellite's lock does — and is told over `SET_GENAU_F_MODE`, since it rescans its own folder rather than playing a list; its F-Mode is its own, apart from the Main Funestra's, the way its browse order is

Every Funestra carries its own F button in the first row of icons on its own HUD (the satellites' control band, the main console's transport row), with that Funestra's reset button just past it. The dashboard's F button, the `F` key and the spoken "all f mode" reach all three at once — they turn F-Mode **on** unless every Funestra is already in it, so the whole-room gesture can never leave half the room narrowed. In Origenerator mode the portrait and Landscape Funestras' part of it is each slideshow's own F-Mode switch, and the dashboard's F button lights off those. A bare spoken "f mode" reaches the Funestra last addressed, and naming a Funestra narrows just that one: "portrait f mode", "f mode landscape", "main f mode on", "both f mode off" — either word order, and `both` means the two satellites. A satellite also takes its side's "favorites" — "portrait favorites", "landscape favorites" — as its F-Mode, the word Origenerator's shows answer with their own F-Mode switch in Origenerator mode.

At startup a session with nothing to resume writes the satellites' playlists with F-Mode off (`build_satellite_playlists`), and every session writes the Main Funestra's list from the F-Mode and the other buttons it carries (`fun_time.main_list`); `apply_fmode` rebuilds the named Funestras after that.

Because the narrowing is invisible in the playlist itself, every HUD says when it is on. Each satellite's status line carries `F-Mode` between the browse order and the act filter (`fun_time/lock_hud.py`), and the Main Funestra's mode HUD carries it beside the length mode or compilation — the Main Funestra is told over `SET_F_MODE`, since a playlist of scripted videos looks like any other. Each HUD's F button lights green off the same per-player flag, published with the rest of that Funestra's panel.

F-Mode is also one of the things a **reset** drops — see below.

### Reset

"Reset" means the same thing on every Funestra: put it back to its defaults, and it is a button on that Funestra's own HUD (next to F-Mode in each satellite's control band and in the main console's transport row) as well as a spoken word. What the defaults cover differs by Funestra, because what each Funestra has does:

- a satellite (`portrait_reset` / `landscape_reset` / `both reset`) releases its lock, clears its act filter, drops F-Mode, ends any group loop with the widened seed row and frozen map that rode on it, returns the browse to shuffled and starts from the top of a freshly built playlist, and puts its playback speed back to 1×. A Funestra whose speed is the only thing off keeps its browse and the video on screen, as the Main Funestra does. `no filter` is the narrow counterpart — it drops the act filter and leaves the rest standing
- the Main Funestra (`main_reset`, "main reset" / "reset main") puts back every button its one list is built from — F-Mode off, 2D and VR both in, clips and full videos both in, the browse shuffled and dealt afresh — which leaves any compilation with it, and starts from the top of the new list; it also cancels any A/B loop, unlocks it and puts its playback speed back to 1×

**A reset does nothing to a Funestra that is already in one.** Every rebuild after it reshuffles and lands on the new list's first entry, so a press with nothing to put back was the quickest way to keep changing what was playing — press it three times and see three videos. A satellite is skipped whole when its side state is already the default one; the Main Funestra's list is rebuilt only when one of those buttons was narrowing or ordering it or a compilation was playing, and the Main Funestra ignores being told to go somewhere it already is.

Bare "reset" reaches whichever Funestra was last addressed, the Main Funestra included.

**A reset with nothing to put back is drawn faded, and takes no press.** The same answer that skips the dispatch rides out to the surfaces that draw the button: the main console's reset, each satellite HUD's, and the dashboards' Reset All go dim while every Funestra they reach is already at its defaults. The Funestras draw that from the button's own dim flag, which fades the mark, leaves it unlit under the pointer and posts nothing. Reset All stays live while a show is hosted — the session cannot see what the show is showing, so it never claims there is nothing to put back.

**Reset All** resets every Funestra at once: it is the dashboard's button beside F-Mode, on the desktop and in the headset alike, and the spoken "reset all" / "all reset". It posts `all_reset`, which the dispatch loop expands the way it expands a `both_*` command — into `main_reset`, `portrait_reset` and `landscape_reset`, each handled exactly as if it had been pressed on its own Funestra.

### Cycle action & cycle seed (satellites)

AI videos under the regen media root carry metadata sidecars (see `regen.media_root` / `metadata_root` in the config) recording the prompts, settings, and seeds they were generated from. Fun Time groups the satellite libraries by that metadata (`fun_time/media_metadata.py`):

- an **action group** is every video generated from the *same source image* — the same subject(s) and setting across different actions (for text-to-video, the same prompt+seed with a different action)
- a **seed family** is every video whose generation config differs *only by seed* — the same scenario rendered from a different seed
- a **loose seed family** is the same scene held only by its prompts and cast/action, with the render settings (model, resolution, aspect ratio, quality, creativity) freed as well as the seed — a wider net for "the same scene, however it was rendered"

Two command pairs ride on those groups, spoken rather than key-bound ("portrait action", "portrait seed", "landscape action", "landscape seed"):

- **Cycle action** switches the current video to the next action of its group, in a fixed order so repeating it tours every act. The log panel names the action that came up. If the sibling is not in the playlist (grouped builds keep one slot per group — see below), it is swapped in place of the current entry.
- **Cycle seed** jumps to a same-config-different-seed sister, touring the family in seed order — preferring the sisters' existing playlist entries. When no exact sister exists, it widens the net to the loose seed family (same scene, render settings freed) so a config that differs only in a render setting still surfaces instead of dead-ending on "No other seeds". Every hit narrates itself over the Funestra and in the log panel — **"Next seed"** for an exact same-config sister, **"Similar clip"** for a widened near-match — so you can see at a glance which one fired (and thus watch the widening happen: say "seed" across clips and wait for a "Similar clip").

Unlike prev/next, cycling does **not** release an active lock: it means "show me this differently", so the lock's repeat-one simply carries over to the sibling.

Both groupings are only as good as the acts the sidecars record, so there is a way to say one is wrong: **"wrong action"** (sided or bare, like "weird") strikes `video.action` out of the current clip's sidecar and keeps what it said under `video.wrong_action`. This is the one edit Fun Time makes to a sidecar. A clip with no act reads as one still needing one, so it comes back around in Evolver's Backfill Metadata tool to be named again — and the `wrong_action` key is how that tool tells a clip you rejected from one nobody ever labeled, so it asks about the rejected ones first, whatever source they came from. Nothing about playback changes: the clip is not bad, only mislabeled.

During satellite builds, each action group **collapses to one playlist slot**, so the same subject+scene doesn't recur once per action. Shuffled builds draw that item weighted by the watch weight below; Premiere (`P`, newest-first) instead keeps the group's newest item and orders by recency. Either way it's one entry per group. Videos without a metadata sidecar behave exactly as before.

### Watch weight — videos "breed" by attention

Fun Time records how you treat each satellite video (`fun_time/watch_stats.py`, persisted in `state/watch_stats.json`):

- playing a video through to ~the end counts a **completion**; while locked on repeat, every loop counts again
- pressing next/prev (or cycling action/seed) early in a video counts a **skip**
- **locking** a video is the strongest positive signal

Warm Gun, on the phone, records the same three for what it plays. Evolver's Watch Weights stage sums the two onto every library video's metadata sidecar as a `watch` block — the counts and a playback weight, `2^((completions + 3·locks − skips)/3)` clamped to between ⅛× and 8× — and Fun Time reads that weight off the sidecar at every shuffled satellite build: weighted shuffle order (loved videos surface early), weighted pick inside collapsed action groups (the acts you finish win the slot), and probabilistic inclusion (a weight-⅛ video sits out ~7 of 8 builds). So what you skip on the phone fades here too, and the other way round. This is the continuous companion to mark-as-weird: hated videos fade away instead of leaving. Neutral videos are never excluded, the transitions the system causes itself (unlock's auto-advance, discards) never penalize anything, and a video Evolver has not stamped yet weighs 1. The same stage carries favorites both ways: a favorite made on the phone lands in `favs.csv`, and every row of `favs.csv` is flagged `favorite` on its sidecar for the phone.

Check the current standings any time with the leaderboard (from the project root):

```bash
./.venv/Scripts/python.exe -m fun_time.breeding_report
```

It ranks every stamped clip in the configured library by that weight — "Rising" then "Fading" — with its action, image seed, and prompt pulled from the same sidecar. Columns: **WEIGHT** is the shuffle-frequency multiplier; **C** = completions (full watches — one per repeat loop while locked), **L** = locks, **S** = skips, **O** = orientation (P portrait / L landscape). Options: `--top N` (rows per section, default 15), `--all`.

## Favorites CSV behavior

Every lock adds the item it holds to `favs.csv`, through one routine
(`fun_time.lock.a_lock_holds`), however it was asked for:

- a satellite's lock key, its padlock button, or "lock" said to it
- a double-click on a thumbnail in a satellite's HUD, locked or not
- a loop button on a video with no siblings, which locks it
- the Main Funestra's lock in Kino mode, and Genau's in Genau mode

The Main Funestra and Genau decide what a press holds, so their item is the one
each reports holding once it has read the press (`fun_time.lock_follower`). The
room's own holds — a session reopening locked, a loop left with one video by a
discard, Genau holding its flick when no shape is left — add nothing.

A lock also counts as a lock in the watch stats, and opens where more of the
item is made: a video a website made opens that site's page in the Random Favs
Browser, putting the browser back in its place first when Origenerator holds it,
and anything else is offered to the hosted Origenerator as `GO_TO|<file>`, which
comes up over the browser on the item in its folder when it made it.

The CSV contains two columns:

- `local_file`
- `web_url`

These are written as spreadsheet-friendly hyperlink formulas so they are clickable in LibreOffice Calc.

If an item is later discarded, it is removed from `favs.csv`.

## Runtime files in `state/`

### `broker_mode.txt`

Written by the broker (the `../broker` project).

Values:

- `0` = Genau takeover not active (the Main Funestra owns playback)
- `1` = Genau takeover active

The audio companion and the Python dispatch loop both read this file as the authoritative source of whether Genau takeover is actually active.

### `genau_cmd.txt`

Written by `fun_time/command_dispatch.py` when Genau or Robot Hand commands are dispatched.

The verbs it may carry are the registry in `../funestra`'s `funestra/genau_controls.py`.

Human-inspired motion replaces the waveform with what real scripts do, inside the
Amp and Center bars' range and at the Speed bar's pace. The flick interval is how
long a flick holds the screen before Genau moves on — 8–12 seconds unless
`FLICK_SECONDS <seconds>` names a pace. It is spelled for what the number is rather than for
the auto-advance that spends it, because that is the word the reference shows
and the phrase a speaker says ("flick seconds thirty", in whatever word the
`genau_flick_words` in `~/.haglio/settings.json` gives Genau's flicks). The interval keeps
counting while the room is paused, so OmniPause leaves the flick on screen where
the user left it. `TOGGLE_LOCK` (the `,` key) pins the current flick while the
interval runs on around it; `WEIRD` condemns the flick, moving the file to the
same place in the `weird` folder of Genau's folder (`genau_dir`) that it had in
the flicks folder (a `2D/AI` flick to `weird/2D/AI`) and taking up its successor.

Genau, on the Main Funestra, consumes and clears this file.

### `main_player_cmd.txt`

Written by the Python dispatch loop when the Main Funestra control commands are dispatched; the Main Funestra consumes and clears it.

Commands (the full set `main_funestra/controls.py` answers to). The verbs every Funestra in the family shares — stepping, the hold, `PLAY_FILE`, `RELOAD_PLAYLIST`, the sound, the rate, the display, `QUIT` — are spelled once in `funestra.funestra_verbs` (see "The Funestra contract" in `../funestra`'s README) and the satellites answer the part of that set that is about the list and the clip: `NEXT`, `PREV`, `SEEK_FWD`, `SEEK_BACK`, `LOCK_ON`, `LOCK_OFF`, `TRASH`, `PLAY_FILE`, `RELOAD_PLAYLIST`, `QUIT`, and `NEXT_VERSION` / `PREV_VERSION`, each carrying the clip's renditions as `path|path` so the Funestra steps from the file it has up and puts the one it lands on in the clip's own place in the list. The Main Funestra's own verbs are spelled in its registry:

- `NEXT` / `PREV`
- `SEEK_FWD` / `SEEK_BACK`
- `SPEED_UP` / `SPEED_DOWN` / `SET_SPEED min|max|<rate>`
- `SET_VOLUME <0-100> [muted]` — the level to *show* plus whether it is muted; the Main Funestra derives the audible loudness (see "Sound")
- `RECORD_DOWN` / `RECORD_UP` / `RECORD_TAP`
- `LOOP_CANCEL`
- `CYCLE_VERSION` / `CYCLE_VERSION_BACK`
- `PLAY_FILE video[TAB]funscript`
- `RELOAD_PLAYLIST`
- `PLAY_COMPILATION` / `END_COMPILATION` / `PLAY_FULL_VID` / `PLAY_CLIP_JUMP`
- `JUMP_TO_FUNSCRIPT` / `NEXT_FUNSCRIPTED` — funscript navigation: seek past this video's quiet stretch to where its scripting starts up again, or leave for the next scripted video in the playlist, landing where its action begins. The Main Funestra alone can answer either, holding both the playlist's funscript column and the parsed script of what is playing
- `SET_TCODE_ENABLED 0|1`
- `SET_MAX_INTENSITY <0-100>` — how far the OSR2 may travel in a second; the Main Funestra shallows its script under it (see "Max intensity")
- `SET_F_MODE 0|1` / `SET_LENGTH_MODE mixed|clips|full|none` / `SET_ACTIVE 0|1` — state only the orchestrator holds and the Main Funestra cannot work out for itself; each drives what its HUD shows. The first two ride beside every `RELOAD_PLAYLIST`, since they say what the list it reloads was narrowed to
- `SHOW kino` / `SHOW genau` — which of Kino and Genau has the Main Funestra's window; the other keeps running out of sight. A mode switch sends it, and Genau's own verbs still arrive on Genau's channel
- `QUIT`

### `main_player_paused.txt`

Flag file — the Main Funestra's pause channel. Mode switches and OmniPause write it; the Main Funestra polls it every tick.

### `main_player_status.txt`

Written by the Main Funestra: the current `video`, `position_ms`, `duration_ms`, `has_funscript`, `state`, `paused`, and `speed` (the rate the video plays at). Read by `genaumacher_save` (for the current video/time in Kino mode), by the dashboard, and by the dispatch loop, which sets both satellites to `speed` whenever it changes.

### `watch_stats.json`

Per-video watch counts (`completions` / `skips` / `locks`) keyed by normalized path — Fun Time's half of the weighting described under "Watch weight"; Evolver reads it, adds the phone's, and writes the result on each sidecar. Written by the dispatch loop's ~1 Hz satellite sampler and by lock commands; entries whose file vanished (e.g. marked weird) are pruned on the next write. Delete the file to drop Fun Time's counts from the next stamping.

### `library_browser_pick.txt`

The video the library browser picked, written as it closes and consumed by the
dispatch loop, which turns it into the Main Funestra's `PLAY_FILE`. The browser is a separate
process (the bridge has no Qt event loop), so this is how the pick gets back.
Cleared before every browse, so abandoning one never replays the last pick.

### `main_player_playlist.tsv`

One video per line, with a TAB plus the funscript path when one exists. The Main Funestra's one list, written by `fun_time.main_list` alone: at every session's start, and whenever a button it is built from is pressed (2D, VR, clips, full videos, F-Mode, Latest, Shuffle, the reset), each write queueing `RELOAD_PLAYLIST` with `SET_F_MODE` and `SET_LENGTH_MODE` on the Main Funestra's command file. No session reads it back: the next one builds it again from the buttons ([docs/resuming-a-session.md](docs/resuming-a-session.md)). Every playlist is written and read through `funestra.playlist`, one `PlaylistItem` per line.

### `origenerator_cmd.txt`, `origenerator_paused.txt`, `origenerator_status.txt`, `origenerator_portrait_hud.json`, `origenerator_landscape_hud.json`

The hosted Origenerator's channel, spoken in the same idioms as the satellites'
(`funestra.file_channel`). The command file carries whatever is said to a
side, exactly as it was said (`portrait_next`, `landscape_lock`,
`portrait_fmode`, …) — the app declared those buttons, so it is what answers
them — plus `portrait_open_slideshow` / `landscape_open_slideshow` (that Funestra
entering its Origenerator mode, which fills it with its base state),
`portrait_close_slideshow` / `landscape_close_slideshow`, `CLOSE_SLIDESHOWS` (a crossing
parking the app, which lets go of both), `QUIT` and `RELEASE`, `PORTRAIT_SAY:<phrase>` for a
spoken command the session heard about one side, and `GO_TO|<file>` once a lock
holds an item no website made (see "Favorites CSV behavior"), which the app
answers only for an item it made: it asks for its place over the Random Favs
Browser (`origenerator_activate` on the dashboard's channel) and goes to the item
in its folder. The paused flag freezes its shows for OmniPause. The status file says only
that the app is up (`ready=1`), which is what lights every Origenerator button. A
show the app is asked for on a Funestra still in Kino mode posts
`portrait_origenerator_activate` / `landscape_origenerator_activate` on the
dashboard's channel and opens once the session hands that Funestra over. A side the app
has let go of is one whose panel it has published empty; the session waits for
that, up to three seconds, before handing that Funestra its own list back — kept
meanwhile in `portrait_playlist.kept.tsv` / `landscape_playlist.kept.tsv`.

The app is handed each Funestra's own playlist, command and status files at
launch, and publishes the panel for each Funestra it holds to its
`origenerator_<side>_hud.json`; the session puts its own mode row over that
panel and publishes the result to the Funestra's HUD file.

### `event_log.jsonl`

Every line any `fun_time` logger emits during a session, one JSON object per line:

```json
{"ts": 1752000000.5, "level": 25, "source": "portrait", "msg": "No other seeds"}
```

`source` is the window the line is about — `main`, `portrait`, `landscape`, `dash`, or `system` for the session at large. `level` is a standard `logging` level plus **`NOTICE` (25)**, the tier for messages meant for whoever is watching the screen ("Clip saved", "Similar clip"). These used to flash as AutoHotkey tooltips under the mouse pointer; now they flash over the Funestra they name (top-center) and land here in the stream.

The file is truncated when a session starts, so it always holds exactly the current run. The log panel tails it — and so can you, or an agent debugging a session: pause, describe the symptom, and the answer is in this one file.

### Log files

The Python entry points also write rotating logs in `state/`:

- `state/orchestrator.log`
- `state/windows_bridge.log`
- `state/genau_audio.log`

The broker and Genau write their own logs (e.g. `broker.log`, `genau_listener.log`, `genau_crash.log`) from the `../broker` and `../genau` projects.

## Notes on design

### Why the broker exists

Windows serial ports are effectively single-owner, so exactly one process can own the OSR2's port.

With the broker:

- the broker alone opens the OSR2's real port
- The Main Funestra and Genau drive the OSR2 by sending T-Code to the broker over UDP; the broker forwards it and suppresses serial input while UDP flows
- Genau gets mode/timing info over localhost instead of serial

(Historically, MultiFunPlayer sat on the other side of a `com0com` virtual serial pair from the broker; MFP and that pair are gone now that UDP T-Code is the only control path.)

### Why Genau and the Main Funestra use local files for commands/mode

For this setup, file-based signaling turned out to be a reliable and simple way to let:

- Python dispatch loop
- broker
- Genau
- The Main Funestra

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

### The OSR2 does not respond to the Main Funestra

Check:

- broker is running
- scheduled task `FunTime Genau Broker` is present and running (`Get-ScheduledTask -TaskName "FunTime Genau Broker"`)
- the broker's log says the OSR2 is plugged in (`The OSR2 is plugged in on COMn`), not that it is waiting for it
- the current video actually has a funscript (`state/main_player_status.txt` shows `has_funscript`) — videos without one play with no OSR2 output by design
- the broker's log (in `../broker`) for serial/COM-port errors

### Genau never appears

Check:

- broker is running
- OSR2 is actually entering auto/free mode
- `state/broker_mode.txt` changes to `1`
- the broker's log (in `../broker`) for serial parsing / mode transitions
- Genau's log (in `../genau`) for UI/runtime errors

### Broker will not start

The broker is the `../broker` project — check its logs and config there. Also confirm the OSR2's USB cable is plugged in: the broker's log says which port it found the OSR2 on, or that it is waiting for one.

### `M` and `.` do not switch Genau flicks

Check:

- `state/broker_mode.txt` is `1`
- `state/genau_cmd.txt` is being written
- flick files exist in the `2D` folder of the configured Genau flicks folder (`paths.flicks_dir`)
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

The broker, Genau, and Genaumacher are separate projects: `../broker`, `../genau`, `../genaumacher`.

## Refactors completed

Completed from the earlier cleanup list:

- orchestration now lives in `fun_time/orchestrator.py`, with `main.sh` kept as a thin wrapper
- config is centralized in `fun_time_config.json`
- Genau, the broker, and Genaumacher have been extracted to their own sibling projects (`../genau`, `../broker`, `../genaumacher`)
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

Run only tests whose name matches a keyword (e.g. just the genaumacher tests):

```bash
bash test.sh -k genaumacher
```

`test.sh` is a thin wrapper around `.venv/Scripts/python.exe -m pytest`. Any extra arguments are forwarded to pytest directly.

### Test layout

| File | What it covers |
|---|---|
| `tests/test_config.py` | `fun_time.config` — loading, validation, derived properties |
| `tests/test_logging_utils.py` | `fun_time.logging_utils` — handler setup, exception hooks |
| `tests/test_orchestrator.py` | `fun_time.orchestrator` — arg parsing, path checks, windows-bridge arg building |
