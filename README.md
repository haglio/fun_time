# Fun Time

Fun Time is a Windows desktop setup that launches and coordinates:

- a primary VLC instance
- two secondary VLC instances
- MultiFunPlayer (MFP)
- a serial broker for the OSR2
- Robot Hand (a clip-based visualizer for OSR2 auto mode)
- a Robot Hand audio companion
- an AutoHotkey controller for window placement and hotkeys

It is designed so that:

- in **control mode**, MFP controls the OSR2 and the primary VLC is visible/active
- in **Robot Hand mode** (OSR2 auto/free mode), Robot Hand appears in front of the primary VLC, the primary VLC pauses, and Robot Hand takes over visual/audio playback
- when auto/free mode ends, Robot Hand hides and the primary VLC resumes

## Folder layout

Core files:

- `main.sh` — main launcher/orchestrator
- `launch.vbs` — hidden Windows launcher used by the shortcut/taskbar item
- `controller.ahk` — AutoHotkey controller and hotkeys
- `broker.py` — serial broker between MFP and the real OSR2
- `robot_hand_listener.py` — Robot Hand UI / clip player
- `robot_hand_audio_companion.py` — Robot Hand audio player
- `icon.ico` — Fun Time / Robot Hand icon

Asset folders:

- `clips/` — Robot Hand video clips
- `audio/` — Robot Hand audio files

Runtime state:

- `state/robot_hand_mode.txt`
- `state/robot_hand_cmd.txt`

Local runtime data:

- `favs.csv` — favorites CSV written when a satellite VLC is locked
- `Fun Time.lnk` — convenience shortcut

## Recommended project-local paths

`favs.csv` should live inside the project folder, not in the old top-level location.

Recommended:

- `C:\path\to\suite-root\projects\fun_time\favs.csv`

It should be ignored by Git.

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
- plays clips from `clips/`
- switches audio from `audio/`

## Clip and audio naming

`clips/` and `audio/` are matched by filename stem.

Example:

- `clips/Daisy.mp4`
- `audio/Daisy.mp3`

and

- `clips/Bella_quarter_middle.mp4`
- `audio/Bella_quarter_middle.mp3`

So the clip and audio files should have the same base name.

## Requirements

### Windows apps

- VLC
- MultiFunPlayer
- AutoHotkey v2
- Git Bash
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

### Normal way

Use the `Fun Time` shortcut / taskbar launcher, which calls:

- `launch.vbs`
- which runs `main.sh`

### Direct test run

From Git Bash:

```bash
cd "/c/path/to/suite-root/projects/fun_time" && bash -x ./main.sh
```

This is the best way to debug startup failures.

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

1. Put the clip video in `clips/`
2. Put the matching audio file in `audio/`
3. Make sure both have the same stem

Example:

- `clips/NewClip.mp4`
- `audio/NewClip.mp3`

No config file is needed for this.

## Making audio from a source video

Example:

```bash
ffmpeg -y -i "source_video.mp4" -vn -c:a libmp3lame -q:a 2 "audio/NewClip.mp3"
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

Run manually in Git Bash:

```bash
cd "/c/path/to/suite-root/projects/fun_time" && bash -x ./main.sh
```

Also verify the shortcut’s **Start in** points to the project folder.

### MFP does not control the OSR2

Check:

- MFP is on `COM14`
- broker is running
- `com0com` pair exists (`COM14` / `COM15`)
- OSR2 is still on `COM4`

### Robot Hand never appears

Check:

- broker is running
- OSR2 is actually entering auto/free mode
- `state/robot_hand_mode.txt` changes to `1`

### `[` and `]` do not switch Robot Hand clips

Check:

- `state/robot_hand_mode.txt` is `1`
- `state/robot_hand_cmd.txt` is being written
- clip files exist in `clips/`

### A clip stutters badly

The clip is probably too large in pixel dimensions. Make a smaller version and use that instead.

## Git / local-only files

These should generally be ignored:

- `state/`
- `archive/`
- `*.lnk`
- `favs.csv`

Depending on your workflow, you may also eventually want to ignore `clips/` and `audio/` if they are large local assets rather than source.

## Current source of truth

These are the files that define the working system:

- `main.sh`
- `launch.vbs`
- `controller.ahk`
- `broker.py`
- `robot_hand_listener.py`
- `robot_hand_audio_companion.py`

## Future cleanup ideas

Potential future refactors:

- replace `main.sh` with a Python orchestrator
- centralize config into one file
- split `robot_hand_listener.py` into smaller modules
- make window/layout constants configurable
- improve logging and diagnostics
