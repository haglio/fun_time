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
- `scripts/run_broker_service.ps1` — broker runner for scheduled-task usage
- `scripts/install_broker_startup_task.ps1` — installs the Windows startup scheduled task for broker
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
  - MFP connects to `COM14`
  - broker connects to `COM15`
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

`fun_time.orchestrator` no longer launches the broker process.

### Clipper way

Use the `Clipper` shortcut at `fun_time/robot_hand/clipper/Clipper.lnk`.

- It follows the same launcher chain as Fun Time: `cmd.exe` -> `wscript.exe` -> `fun_time/robot_hand/clipper/launch_clipper.vbs`
- The VBS runs `python -m fun_time.robot_hand.clipper` from project root

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

MFP should use:

- `COM14`

not `COM4`.

The real OSR2 remains on:

- `COM4`

The broker sits between them using `COM15`.

## Hotkeys

Primary controls:

- `Esc` — close everything
- `Space` — pause/play the primary VLC

Mode-dependent keys:

- `[`
- `]`

Behavior:

- in control mode, `[` / `]` control the primary VLC
- in Robot Hand mode, `[` / `]` cycle Robot Hand clips
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

Written by AutoHotkey when `[` or `]` are pressed during Robot Hand mode.

Values:

- `PREV`
- `NEXT`

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

- MFP is on `COM14`
- broker is running
- `com0com` pair exists (`COM14` / `COM15`)
- OSR2 is still on `COM4`
- scheduled task `FunTime Robot Hand Broker` is present and running (`Get-ScheduledTask -TaskName "FunTime Robot Hand Broker"`)

### Robot Hand never appears

Check:

- broker is running
- OSR2 is actually entering auto/free mode
- `state/robot_hand_mode.txt` changes to `1`
- `state/broker.log` for serial parsing / mode transitions
- `state/robot_hand_listener.log` for UI/runtime errors

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
