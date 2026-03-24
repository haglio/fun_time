# Fun Time

Fun Time is a Windows desktop setup that launches and coordinates:

- a primary VLC instance
- two secondary VLC instances
- MultiFunPlayer (MFP)
- Robot Hand (a clip-based visualizer for OSR2 auto mode)
- a Robot Hand audio companion
- an AutoHotkey controller for window placement and hotkeys

It uses a serial broker for the OSR2 that is intended to run continuously in the background.

It is designed so that:

- in **control mode**, MFP controls the OSR2 and the primary VLC is visible/active
- in **Robot Hand mode** (OSR2 auto/free mode), Robot Hand appears in front of the primary VLC, the primary VLC pauses, and Robot Hand takes over visual/audio playback
- when auto/free mode ends, Robot Hand hides and the primary VLC resumes

## Folder layout

Core files:

- `main.sh` — compatibility wrapper that forwards to `orchestrator.py`
- `launch.vbs` — hidden Windows launcher used by the shortcut/taskbar item
- `fun_time_config.json` — central config for paths, ports, and layout values
- `controller.ahk` — AutoHotkey controller and hotkeys
- `fun_time/` — shared Python package for config, logging, orchestration, and Robot Hand modules
- `scripts/run_broker_service.ps1` — broker runner used behind the tray launcher
- `scripts/install_broker_startup_task.ps1` — installs the Windows startup scheduled task for broker
- `launch_broker_tray.vbs` — hidden Windows launcher for the broker tray app
- `icon.ico` — Fun Time / Robot Hand icon

Asset folders:

- `fun_time/robot_hand/clips/` — Robot Hand video clips
- `fun_time/robot_hand/audio/` — Robot Hand audio files

Runtime state:

- `state/robot_hand_mode.txt`
- `state/robot_hand_cmd.txt`
- `state/*.log`

Tooling:

- `fun_time/robot_hand/clipper/` — CLI helper module for preparing files into `clips/` and `audio/`

Local runtime data:

- `favs.csv` — favorites CSV written when a satellite VLC is locked
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
- Robot Hand playback defaults
- monitor/layout ratios used by `controller.ahk`

Useful sections:

- `paths`
- `controller.layout`
- `broker`
- `robot_hand`
- `audio_companion`

For the satellite AI libraries, Fun Time can now read either a single folder or multiple folders:

- `paths.portrait_dir` or `paths.portrait_dirs`
- `paths.landscape_dir` or `paths.landscape_dirs`

If the list form is used, the portrait or landscape VLC gets all listed folders joined into one rotating source set.

Primary VLC source folders are configured with:

- preferred: `paths.primary_vlc_dirs` (list of one or more folders)

Example:

```json
{
  "paths": {
    "primary_vlc_dirs": [
      "C:/videos/set_a",
      "C:/videos/set_b"
    ]
  }
}
```

`robot_hand.shuffle_on_load` defaults to `true`, which randomizes clip order once at load time.

To disable shuffle and use filesystem order:

```json
{
  "robot_hand": {
    "shuffle_on_load": false
  }
}
```

The layout values that used to be hard-coded in AutoHotkey now live under `controller.layout`.

## High-level architecture

Serial / mode control:

- OSR2 real device is on `COM4`
- `com0com` virtual pair is used:
  - historically this was `COM14` / `COM15`
  - on this machine it may be recreated by Windows / `com0com` under different COM numbers later
  - MFP should use the `CNCA*` side of the pair
  - broker should use the matching `CNCB*` side of the pair
- `broker.py` is the only process that talks to the real OSR2

The broker:

- forwards MFP serial traffic to the OSR2 in control mode
- swallows MFP writes while OSR2 auto/free mode is active
- watches OSR2 output for:
  - `freeMode is on!`
  - `freeMode is off!`
  - `freeMode tcode task started`
  - `freeMode tcode task is stopped`
  - `StrokeName: ...`
  - `bpm ...`
- sends lightweight localhost messages to Robot Hand
- if the configured virtual COM port is missing, it now tries to recover by detecting the current `com0com` broker-side port automatically

Robot Hand:

- does **not** open `COM4`
- listens for broker-fed state
- shows itself only in Robot Hand mode
- hides itself otherwise
- plays clips from `fun_time/robot_hand/clips/`
- switches audio from `fun_time/robot_hand/audio/`

## Clip and audio naming

`fun_time/robot_hand/clips/` and `fun_time/robot_hand/audio/` are matched by filename stem.

Example:

- `fun_time/robot_hand/clips/Daisy.mp4`
- `fun_time/robot_hand/audio/Daisy.mp3`

and

- `fun_time/robot_hand/clips/Bella_quarter_middle.mp4`
- `fun_time/robot_hand/audio/Bella_quarter_middle.mp3`

So the clip and audio files should have the same base name.

## Requirements

### Windows apps

- VLC
- MultiFunPlayer
- AutoHotkey v2
- `com0com`

### Python / tools

- Python (currently launched via Miniconda `pythonw.exe`)
- `pyserial`
- `pygame`
- `Pillow`
- `ffmpeg`
- `ffprobe`

Example installs:

```bash
python -m pip install pyserial pygame pillow
```

`ffmpeg` / `ffprobe` should be available on `PATH`.

## Launching

### Broker startup task (one-time setup)

Install the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_broker_startup_task.ps1
```

If Scheduled Task creation is denied by Windows permissions, the installer automatically falls back to a per-user Startup launcher.

Start it immediately (optional):

```powershell
Start-ScheduledTask -TaskName "FunTime Robot Hand Broker"
```

After setup, broker starts automatically when you sign in to Windows.

Remove broker autostart (Scheduled Task and Startup fallback):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_broker_startup.ps1
```

### Normal way

Use the `Fun Time` shortcut / taskbar launcher, which calls:

- `launch.vbs`
- which runs `python -m fun_time.orchestrator`

`fun_time.orchestrator` now starts the broker tray launcher if the broker is missing, so the tray status icon and broker recovery flow stay aligned with Windows logon startup.

### Clipper way

Use the `Clipper` shortcut at `fun_time/robot_hand/clipper/Clipper.lnk`.

- It follows the same launcher chain as Fun Time: `cmd.exe` -> `wscript.exe` -> `fun_time/robot_hand/clipper/launch_clipper.vbs`
- The VBS runs `python -m fun_time.robot_hand.clipper` from project root

Clipper exit prompt behavior:

- `Tab` cycles the pending exit action
- `Enter` confirms the currently outlined exit action
- `Esc` always cancels the exit prompt
- the selected action is shown with a highlighted border instead of a filled button

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

## MFP setup

### One-time `com0com` concept

Fun Time expects a virtual serial pair created by `com0com`.

That pair has two ends:

- `CNCA*` side: this is the side MFP should connect to
- `CNCB*` side: this is the side the broker should connect to

Historically this project used:

- MFP = `COM14` (`CNCA2`)
- broker = `COM15` (`CNCB2`)

But that is **not guaranteed forever**. If the pair is removed and recreated, Windows may assign different COM numbers and even a different `CNCA` / `CNCB` index, for example:

- MFP = `COM7` (`CNCA1`)
- broker = `COM8` (`CNCB1`)

What matters is the pairing, not the exact COM numbers:

- MFP must use the `CNCA*` side
- broker must use the matching `CNCB*` side
- broker still talks to the real OSR2 on `COM4`

### Normal expected setup

MFP should use:

- the `CNCA*` side of the current `com0com` pair

not `COM4`.

The real OSR2 remains on:

- `COM4`

The broker sits between them using the matching `CNCB*` side.

### Where MFP stores its serial choice

MultiFunPlayer stores its selected serial device in:

- `C:\Program Files\MultiFunPlayer-1.33.9-patreon\MultiFunPlayer.config.json`

Look for:

- `"SelectedSerialPort": "COM0COM\\PORT\\CNCA..."`

If the old `CNCA` port disappears and `com0com` comes back with a different pair, MFP may still point at the dead one until you update it or reselect the port in MFP.

### If the old pair disappears

Symptoms:

- broker log shows it cannot open the old configured virtual COM port
- MFP no longer controls the OSR2
- Windows shows a different `com0com` pair than the one in old notes

Recovery steps:

1. List current serial ports and identify the `com0com` pair.
2. Point MFP at the `CNCA*` side.
3. Point broker config at the matching `CNCB*` side, or rely on the broker's new auto-detection fallback.
4. Keep `COM4` reserved for the real OSR2 only.

Current broker behavior:

- broker config may still say `COM15`
- if `COM15` is missing, broker now detects the current `CNCB*` port and uses that automatically
- launching Fun Time now also starts the broker if the broker is not already running

This means broker startup is more resilient now, but MFP still needs its saved `CNCA*` selection to be correct.

## Hotkeys

Primary controls:

- `Ctrl+Alt+Q` — close everything
- `Esc` — toggle OmniPause
- `Space` — pause/play the primary VLC
- `-` / `=` — nudge the primary VLC backward / forward by the configured VLC seek amount

Mode-dependent keys:

- `[`
- `]`
- `\`

Behavior:

- in control mode, `[` / `]` control the primary VLC
- in control mode, `\` opens the primary VLC open-file dialog
- in Robot Hand mode, `[` / `]` cycle Robot Hand clips
- in Robot Hand mode, `\` offsets Robot Hand playback by a quarter cycle
- on non-US keyboard layouts, the physical bracket-key positions are also bound
- while Fun Time is running and not OmniPaused, these hotkeys are global and do not depend on which window is active

In control mode, the `\\` action temporarily enters OmniPause while the file dialog is open, then automatically leaves OmniPause when the dialog closes without toggling primary VLC playback state.
- when clip order is shuffled on load (default), `]` then `[` returns to the prior clip within that same loaded order

Robot Hand clip switching wraps around cyclically:
- `]` at the end goes back to the beginning
- `[` at the beginning goes to the end

Secondary VLC controls:

- `Left` / `Right` — previous / next on portrait VLC
- `Up` — discard current portrait item
- `Down` — toggle portrait lock

- `a` / `d` — previous / next on landscape VLC
- `w` — discard current landscape item
- `s` — toggle landscape lock

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

### `robot_hand_mode.txt`

Written by `broker.py`.

Values:

- `0` = control mode
- `1` = Robot Hand mode

AutoHotkey uses this file as the source of truth for whether Robot Hand mode is active.

### `robot_hand_cmd.txt`

Written by AutoHotkey when Robot Hand control hotkeys are pressed during Robot Hand mode.

Values:

- `PREV`
- `NEXT`
- `OFFSET_QUARTER_CYCLE`

`OFFSET_QUARTER_CYCLE` advances Robot Hand playback by one quarter of the current loop.

`robot_hand_listener.py` consumes and clears this file.

### Log files

The Python entry points now write rotating logs in `state/`.

Common log files:

- `state/orchestrator.log`
- `state/controller.log`
- `state/broker.log`
- `state/robot_hand_listener.log`
- `state/robot_hand_audio.log`
- `state/robot_hand_crash.log`

## Notes on design

### Why the broker exists

Windows serial ports are effectively single-owner.

Without the broker:

- MFP and Robot Hand would fight over `COM4`

With the broker:

- MFP uses virtual `COM14`
- broker uses `COM15` and real `COM4`
- Robot Hand gets mode/timing info over localhost instead of serial

### Why Robot Hand uses local files for commands/mode

For this setup, file-based signaling turned out to be a reliable and simple way to let:

- AHK
- broker
- Robot Hand

coordinate mode and clip-switch commands without depending on focused windows.

## Adding a new Robot Hand clip

1. Put the clip video in `fun_time/robot_hand/clips/`
2. Put the matching audio file in `fun_time/robot_hand/audio/`
3. Make sure both have the same stem

Example:

- `fun_time/robot_hand/clips/NewClip.mp4`
- `fun_time/robot_hand/audio/NewClip.mp3`

No config file is needed for this.

## Making audio from a source video

Example:

```bash
ffmpeg -y -i "source_video.mp4" -vn -c:a libmp3lame -q:a 2 "fun_time/robot_hand/audio/NewClip.mp3"
```

## Resizing a clip if Robot Hand struggles with it

If a clip is too heavy, reduce its pixel dimensions.

Example:

```bash
ffmpeg -y -i "Bella_half_middle.mp4" -an -vf "scale=640:-2" -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p "Bella_640_middle.mp4"
```

For an even smaller version:

```bash
ffmpeg -y -i "Bella_half_middle.mp4" -an -vf "scale=480:-2" -c:v libx264 -crf 20 -preset medium -pix_fmt yuv420p "Bella_480_middle.mp4"
```

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
- `state/controller.log`

### MFP does not control the OSR2

Check:

- MFP is on the current `CNCA*` side of the `com0com` pair
- broker is running
- `com0com` pair exists
- broker log shows which broker-side port it chose
- OSR2 is still on `COM4`
- scheduled task `FunTime Robot Hand Broker` is present and running (`Get-ScheduledTask -TaskName "FunTime Robot Hand Broker"`)
- `C:\Program Files\MultiFunPlayer-1.33.9-patreon\MultiFunPlayer.config.json` does not still point at an old dead `CNCA*` device

### Robot Hand never appears

Check:

- broker is running
- OSR2 is actually entering auto/free mode
- `state/robot_hand_mode.txt` changes to `1`
- `state/broker.log` for serial parsing / mode transitions
- `state/robot_hand_listener.log` for UI/runtime errors

### Broker will not start

Check:

- `state/broker.log`
- `state/broker_service_launcher.log`
- current serial ports still include the real OSR2 on `COM4`
- current serial ports still include a `com0com` pair

If `fun_time_config.json` still points at an old broker-side port such as `COM15`, broker now tries to recover automatically by selecting the current `CNCB*` port.

If it still cannot recover, confirm that:

- MFP is on the matching `CNCA*` side
- the `com0com` pair actually exists in Windows Device Manager / serial-port enumeration

### `[` and `]` do not switch Robot Hand clips

Check:

- `state/robot_hand_mode.txt` is `1`
- `state/robot_hand_cmd.txt` is being written
- clip files exist in `fun_time/robot_hand/clips/`
- `state/controller.log` shows the hotkey write
- `state/robot_hand_listener.log` shows command-file consumption errors

### A clip stutters badly

The clip is probably too large in pixel dimensions. Make a smaller version and use that instead.

## Git / local-only files

These should generally be ignored:

- `state/`
- `archive/`
- `*.lnk`
- `favs.csv`

The Robot Hand asset folders are intentionally ignored because they are large local assets rather than source:

- `fun_time/robot_hand/clips/`
- `fun_time/robot_hand/audio/`

## Current source of truth

These are the files that define the working system:

- `fun_time_config.json`
- `main.sh`
- `launch.vbs`
- `controller.ahk`
- `fun_time/config.py`
- `fun_time/orchestrator.py`
- `fun_time/broker_app.py`
- `fun_time/audio_companion_app.py`
- `fun_time/robot_hand/app.py`
- `fun_time/robot_hand/state.py`
- `fun_time/robot_hand/video.py`

## Refactors completed

Completed from the earlier cleanup list:

- orchestration now lives in `fun_time/orchestrator.py`, with `main.sh` kept as a thin wrapper
- config is centralized in `fun_time_config.json`
- Robot Hand is modularized under `fun_time/robot_hand/`
- window/layout constants are configurable through `controller.layout`
- runtime logging and diagnostics are written to `state/*.log`

## Developing

Before doing repo work, consult [AGENTS.md](c:/suite-root/blah/blah/projects/fun_time/AGENTS.md) for the required preflight and the canonical test commands.

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
| `tests/test_orchestrator.py` | `fun_time.orchestrator` — arg parsing, path checks, controller arg building |
| `tests/test_robot_hand_state.py` | `fun_time.robot_hand.state` — `SharedState` defaults, UDP message parsing |
| `tests/test_robot_hand_video.py` | `fun_time.robot_hand.video` — `scan_clips`, supported extensions |
| `tests/test_clipper_utils.py` | `fun_time.robot_hand.clipper.utils` — timestamp parsing, name sanitization, atomic JSON write |
| `tests/test_clipper_state.py` | `fun_time.robot_hand.clipper.state` — `VideoState` properties, mark-in/out, timeline mapping |
| `tests/test_clipper_paths.py` | `fun_time.robot_hand.clipper.paths` — path constants, key bindings, `ensure_runtime_dirs` |
| `tests/test_clipper_export.py` | `fun_time.robot_hand.clipper.export` — ffmpeg clock parsing, output validation, progress tracking |
