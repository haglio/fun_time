# Fun Time

Fun Time is a Windows desktop setup that launches and coordinates:

- Nau, a funscript video player for the primary video library (lives in the separate `../genau` project, launched as `python -m nau`)
- two satellite players (portrait and landscape), launched as `python -m satellite` from the same `../genau` project
- Genau, a clip-based visualizer for OSR2 auto mode (the separate `../genau` project)
- a Genau audio companion
- a minimal AutoHotkey hotkey shell (window placement and command dispatch run in Python)

It uses a serial broker for the OSR2 — the separate `../osr2_broker` project — that is intended to run continuously in the background.

The primary stack runs in one of three modes (startup mode is **nau**):

- in **Nau mode**, Nau owns the primary display and the OSR2: it plays the whole primary library (videos without a funscript just play with no OSR2 output) and drives the OSR2 itself by sending funscript-derived T-Code over UDP to the broker
- in **Genau mode** (OSR2 auto/free mode), Genau clips own both the primary display and the OSR2
- in **Hybrid mode**, Nau displays video under Genau's HUD while Genau drives the OSR2

## Folder layout

Core files:

- `main.sh` — compatibility wrapper that forwards to `orchestrator.py`
- `launch.vbs` — hidden Windows launcher used by the shortcut/taskbar item
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

- `favs.csv` — favorites CSV written when a satellite is locked
- `Fun Time.lnk` — convenience shortcut

## Recommended project-local paths

`favs.csv` should live inside the project folder, not in the old top-level location.

Recommended:

- `C:\path\to\suite-root\projects\fun_time\favs.csv`

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
- `layout`
- `genau`
- `audio_companion`

For the satellite AI libraries, Fun Time can now read either a single folder or multiple folders:

- `paths.portrait_dir` or `paths.portrait_dirs`
- `paths.landscape_dir` or `paths.landscape_dirs`

If the list form is used, the portrait or landscape satellite gets all listed folders joined into one rotating source set.

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

- `main_monitor` — the monitor that shows the landscape satellite, the dashboard, and the Random Favs Browser
- `secondary_monitor` — the monitor that shows the portrait satellite and the shared primary display slot (Nau and Genau use the same rect)

## High-level architecture

Serial / mode control:

- the real OSR2 is on `COM4`; the **broker** — the separate `../osr2_broker` project — is the only process that talks to it. It forwards UDP T-Code to the OSR2 unconditionally and suppresses serial input while UDP flows, watches the OSR2 for free-mode transitions, and publishes mode/timing state over localhost and `state/genau_mode.txt`.
- **Nau** — a funscript video player in the `../genau` project — never opens `COM4`. It drives the OSR2 itself by sending funscript-derived T-Code to the broker over UDP (the same port Genau uses), reads commands from `state/nau_cmd.txt`, and publishes playback status to `state/nau_status.txt`.
- **Genau** — the separate `../genau` project — never opens `COM4` either. It follows the broker-fed state, shows itself only in Genau/Hybrid mode, and reads clip/offset commands from `state/genau_cmd.txt`.

See those projects for the serial parsing, COM-port recovery, and playback internals.

### The log panel

The main monitor's left column stacks the **Dashboard** across its top and the **Random Favs Browser** filling the rest below. The Dashboard spans the full column width: its schematic of the two monitors on the left and the **log panel** embedded in the strip beside it. The schematic still draws all three regions, so the picture matches the screen.

The log panel is a widget inside the dashboard window — one window, not two — so it rides the dashboard's topmost band, minimize/restore and close. It tails `state/event_log.jsonl` and shows the **stream** of everything the session logs, filtered by a verbosity dial (`DEBUG`/`INFO`/`NOTICE`/`WARNING`/`ERROR`, default `NOTICE`) and by per-window toggles across one compact row. Both settings persist in `state/log_panel.ini`.

The brief **notices** — "Clip saved", "No other seeds", "Next seed", "Similar clip" — flash over the top-center of the player they concern (a portrait notice over the portrait satellite, a primary notice over the Nau/Genau display) and then fade. They also land in the stream, coloured by level, so the panel is where you scroll back through them. The flash always fires regardless of the verbosity dial, which governs only the stream. Long lines in the stream **word-wrap** rather than being cut off, so the tail of a message (a video name, a phrase heard) is readable.

Every recognized voice command flashes a **green confirmation** — the phrase it matched — over the player it addresses, so you can see what was heard. A command that hits a dead end ("No other seeds", "No action metadata") flashes **red** instead. And when the recognizer clearly hears speech that matches no command, it flashes **"unrecognized command: ‹what it heard›"** in red — a second, unrestricted recognizer runs alongside the grammar one purely to transcribe that, so an out-of-grammar phrase surfaces as text instead of vanishing.

## Requirements

### Windows apps

- AutoHotkey v2

### Python / tools

- Python (currently launched via Miniconda `pythonw.exe`)
- Python dependencies are declared in `pyproject.toml` — notably PyQt6 (dashboard), pygame-ce (audio companion), vosk + sounddevice (voice control), and Pillow / numpy / opencv-python.
- Genau and Nau run out of the `../genau` project's venv (`paths.genau_python_exe`), launched as `python -m genau` and `python -m nau`.

Install the declared dependencies into the project venv before first use.

## Launching

### Broker startup task (one-time setup)

The broker runs as its own background service from the `../osr2_broker` project — see that project for its one-time startup-task setup (it can autostart at Windows logon). Launching Fun Time also starts the broker tray if it is not already running.

### Normal way

Use the `Fun Time` shortcut / taskbar launcher, which calls:

- `launch.vbs`
- which runs `python -m fun_time.orchestrator`

`fun_time.orchestrator` now starts the broker tray launcher if the broker is missing, so the tray status icon and broker recovery flow stay aligned with Windows logon startup.

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
cd "/c/path/to/suite-root/projects/fun_time" && bash ./main.sh
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

The satellite voice commands can be spoken with or without naming a side. The side word always comes first, so naming one — "portrait lock", "landscape next" — acts on that player as always. Said **bare** — "lock", "unlock", "next", "previous", "weird", "action", "seed" — the command acts on the **active side**: whichever satellite you most recently touched, by voice *or* by keyboard. So if you were just navigating the portrait with `←`/`→`, a plain "lock" locks the portrait; switch to the landscape with `A`/`D` and "lock" now locks the landscape. The active side is remembered (persisted in the bridge's shared state) until the other side is addressed. Bare commands are voice-only — the keys stay side-specific.

### Modes

The primary stack runs in one of three modes, each selected by its own hotkey (see the popup): **Nau**, **Genau**, and **Hybrid**. The `\` key is mode-dependent:

- in Nau mode, `\` opens a file-picker dialog; the chosen video plays in Nau, paired with its funscript when one exists at the mirrored path. Fun Time enters OmniPause while the dialog is open and leaves it when the dialog closes.
- in Genau and Hybrid modes, `\` offsets Genau playback by a quarter cycle.

The `-`/`=` nudge keys and the `[`/`]` prev/next keys drive Nau in every mode (in Genau mode the paused Nau still navigates in the background). The `'` clip-save key reads the current video/time from Nau's status file in Nau and Hybrid modes.

The Nau-mode voice trigger is spoken as "now now" (the reference displays it as "nau nau" — "nau" itself is not in the recognizer's vocabulary).

### Loop recording (Nau mode)

Hold `R` to record: a red dot and a growing filmstrip of one thumbnail per recorded second appear on screen. Release to snap the loop to funscript base positions and start looping (amber loop icon). Press `R` again to cancel back to normal playback (play icon). A small corner icon always shows Nau's play/pause/record/loop state. Voice equivalents: "record", "loop", "cancel".

### OmniPause

- `Esc` toggles OmniPause; `Space` enters it.
- While OmniPaused, the global hotkeys are suspended — only `Esc` (toggle OmniPause) and `Ctrl+Alt+Q` (quit) stay active.

### F-Mode

Toggling F-Mode rebuilds every playlist immediately, rather than waiting for the next advance — the two satellite `.tsv` playlists plus Nau's `nau_playlist.tsv` (Nau is sent `RELOAD_PLAYLIST`) — and restricts playback to funscript-backed media:

- the primary playlist (Nau) keeps only videos that have a matching `.funscript` at the mirrored path, where `videos\videos\…` maps to `videos\scripts\scripts\….funscript`
- each satellite plays only items that are in its normal portrait/landscape pool *and* listed in `favs.csv`

The same builder (`build_fmode_playlists`) writes all three playlist files at startup, so startup and the F-mode toggle share one playlist authority.

### Cycle action & cycle seed (satellites)

AI videos under the provider media root carry metadata sidecars (see `provider_regen.media_root` / `metadata_root` in the config) recording the prompts, settings, and seeds they were generated from. Fun Time groups the satellite libraries by that metadata (`fun_time/media_metadata.py`):

- an **action group** is every video generated from the *same source image* — the same subject(s) and situation doing different things (for text-to-video, the same prompt+seed with a different action)
- a **seed family** is every video whose generation config differs *only by seed* — the same scenario cast with a different subject
- a **loose seed family** is the same scene held only by its prompts and cast/action, with the render knobs (model, resolution, aspect ratio, quality, creativity) freed as well as the seed — a wider net for "the same scene, however it was rendered"

Two command pairs ride on those groups (keys: `Del`/`End` portrait, `E`/`Q` landscape; voice: "portrait action", "portrait seed", "landscape action", "landscape seed"):

- **Cycle action** switches the current video to the next action of its group, in a fixed order so repeated presses tour every act. The log panel names the action that came up. If the sibling is not in the playlist (grouped builds keep one slot per group — see below), it is swapped in place of the current entry.
- **Cycle seed** jumps to a same-config-different-seed sister, touring the family in seed order — preferring the sisters' existing playlist entries. When no exact sister exists, it widens the net to the loose seed family (same scene, render knobs freed) so a config that differs only in a render setting still surfaces instead of dead-ending on "No other seeds". Every hit narrates itself over the player and in the log panel — **"Next seed"** for an exact same-config sister, **"Similar clip"** for a widened near-match — so you can see at a glance which one fired (and thus watch the widening happen: press seed across clips and wait for a "Similar clip").

Unlike prev/next, cycling does **not** release an active lock: it means "show me this differently", so the lock's repeat-one simply carries over to the sibling.

During satellite builds, each action group **collapses to one playlist slot**, so the same subject+scene doesn't recur once per action. Shuffled builds draw that member weighted by the watch stats below; Premiere (`P`, newest-first) instead keeps the group's newest member and orders by recency. Either way it's one entry per group. Videos without a metadata sidecar behave exactly as before.

### Watch stats — videos "breed" by attention

Fun Time watches how you treat each satellite video and adjusts how often it comes up (`fun_time/watch_stats.py`, persisted in `state/watch_stats.json`):

- playing a video through to ~the end counts a **completion**; while locked on repeat, every loop counts again
- pressing next/prev (or a cycle key) early in a video counts a **skip**
- **locking** a video is the strongest positive signal

Counts become a playback weight — `2^((completions + 3·locks − skips)/3)`, clamped to between ⅛× and 8× — applied at every shuffled satellite build: weighted shuffle order (loved videos surface early), weighted pick inside collapsed action groups (the acts you finish win the slot), and probabilistic inclusion (a weight-⅛ video sits out ~7 of 8 builds). This is the continuous companion to mark-as-weird: hated videos fade away instead of leaving. Neutral videos are never excluded, and the transitions the system causes itself (unlock's auto-advance, discards) never penalize anything.

Check the current standings any time with the leaderboard (from the project root):

```bash
./.venv/Scripts/python.exe -m fun_time.breeding_report
```

It ranks every tracked clip by weight — "Rising" then "Fading" — with its action, image seed, and prompt pulled from the metadata sidecars. Columns: **WEIGHT** is the shuffle-frequency multiplier; **C** = completions (full watches — one per repeat loop while locked), **L** = locks, **S** = skips, **O** = orientation (P portrait / L landscape). Options: `--top N` (rows per section, default 15), `--all`.

## Favorites CSV behavior

When a satellite is locked, the current media item is added to `favs.csv`.

Specifically:

- locking the portrait satellite writes its current item to `favs.csv`
- locking the landscape satellite writes its current item to `favs.csv`

The CSV contains two columns:

- `local_file`
- `web_url`

These are written as spreadsheet-friendly hyperlink formulas so they are clickable in LibreOffice Calc.

If an item is later discarded, it is removed from `favs.csv`.

## Runtime files in `state/`

### `genau_mode.txt`

Written by the broker (the `../osr2_broker` project).

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

`OFFSET_QUARTER_CYCLE` advances Genau playback by one quarter of the current loop.

Genau (the `../genau` project) consumes and clears this file.

### `nau_cmd.txt`

Written by the Python dispatch loop when Nau control commands are dispatched; Nau consumes and clears it.

Commands:

- `NEXT` / `PREV`
- `SEEK_FWD` / `SEEK_BACK`
- `RECORD_DOWN` / `RECORD_UP` / `RECORD_TAP`
- `LOOP_CANCEL`
- `PLAY_FILE video[TAB]funscript`
- `RELOAD_PLAYLIST`
- `QUIT`

### `nau_paused.txt`

Flag file — Nau's pause channel. Mode switches and OmniPause write it; Nau polls it every tick.

### `nau_status.txt`

Written by Nau: the current `video`, `position_ms`, `duration_ms`, `has_funscript`, `state`, and `paused`. Read by `clipper_save` (for the current video/time outside Hybrid mode) and by the dashboard.

### `watch_stats.json`

Per-video watch counts (`completions` / `skips` / `locks`) keyed by normalized path — the input to the frequency weighting described under "Watch stats". Written by the dispatch loop's ~1 Hz satellite sampler and by lock commands; entries whose file vanished (e.g. marked weird) are pruned on the next write. Delete the file to reset all weights to neutral.

### `nau_playlist.tsv`

One video per line, with a TAB plus the funscript path when one exists. Written by `build_fmode_playlists` at startup and on every F-mode toggle (which also sends Nau `RELOAD_PLAYLIST`).

### `event_log.jsonl`

Every line any `fun_time` logger emits during a session, one JSON object per line:

```json
{"ts": 1752000000.5, "level": 25, "source": "portrait", "msg": "No other seeds"}
```

`source` is the window the line is about — `primary`, `portrait`, `landscape`, `dash`, or `system` for the session at large. `level` is a standard `logging` level plus **`NOTICE` (25)**, the tier for messages meant for whoever is watching the screen ("Clip saved", "Similar clip"). These used to flash as AutoHotkey tooltips under the mouse pointer; now they flash over the player they name (top-center) and land here in the stream.

The file is truncated when a session starts, so it always holds exactly the current run. The log panel tails it — and so can you, or an agent debugging a session: pause, describe the symptom, and the answer is in this one file.

### Log files

The Python entry points also write rotating logs in `state/`:

- `state/orchestrator.log`
- `state/windows_bridge.log`
- `state/genau_audio.log`

The broker and Genau write their own logs (e.g. `broker.log`, `genau_listener.log`, `genau_crash.log`) from the `../osr2_broker` and `../genau` projects.

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
cd "/c/path/to/suite-root/projects/fun_time" && bash ./main.sh
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
- the broker's log (in `../osr2_broker`) for serial/COM-port errors

### Genau never appears

Check:

- broker is running
- OSR2 is actually entering auto/free mode
- `state/genau_mode.txt` changes to `1`
- the broker's log (in `../osr2_broker`) for serial parsing / mode transitions
- Genau's log (in `../genau`) for UI/runtime errors

### Broker will not start

The broker is the `../osr2_broker` project — check its logs and config there. Also confirm that current serial ports still include the real OSR2 on `COM4`.

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
- `windows_bridge_hotkeys.ahk`
- `fun_time/config.py`
- `fun_time/orchestrator.py`
- `fun_time/command_dispatch.py`
- `fun_time/dashboard_app.py`
- `fun_time/audio_companion_app.py`

The broker, Genau/Nau, and Clipper are separate projects: `../osr2_broker`, `../genau`, `../clipper`.

## Refactors completed

Completed from the earlier cleanup list:

- orchestration now lives in `fun_time/orchestrator.py`, with `main.sh` kept as a thin wrapper
- config is centralized in `fun_time_config.json`
- Genau, the broker, and Clipper have been extracted to their own sibling projects (`../genau`, `../osr2_broker`, `../clipper`)
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
