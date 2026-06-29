# Fun Time

Fun Time is a Windows desktop setup that launches and coordinates:

- a primary VLC instance
- two secondary VLC instances
- MultiFunPlayer (MFP)
- Genau, a clip-based visualizer for OSR2 auto mode (the separate `../genau` project)
- a Genau audio companion
- a minimal AutoHotkey hotkey shell (window placement and command dispatch run in Python)

It uses a serial broker for the OSR2 — the separate `../osr2_broker` project — that is intended to run continuously in the background.

It is designed so that:

- in **VLC mode**, MFP controls the OSR2 and the primary VLC is visible/active
- in **Genau mode** (OSR2 auto/free mode), Genau appears in front of the primary VLC, the primary VLC pauses, and Genau takes over visual/audio playback
- when auto/free mode ends, Genau hides and the primary VLC resumes

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
- `state/audio_paused.txt`
- `state/*.log`

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

- `main_monitor` — the monitor that shows portrait VLC, the primary VLC, and Genau
- `secondary_monitor` — the monitor that shows landscape VLC, MFP, and the Random Favs Browser

## High-level architecture

Serial / mode control:

- the real OSR2 is on `COM4`; a `com0com` virtual pair sits between MFP and the broker (MFP uses the `CNCA*` side, the broker the matching `CNCB*` side)
- the **broker** — the separate `../osr2_broker` project — is the only process that talks to the real OSR2. It forwards MFP serial traffic in VLC mode, swallows MFP writes during OSR2 auto/free mode, watches the OSR2 for free-mode transitions, and publishes mode/timing state over localhost and `state/genau_mode.txt`.
- **Genau** — the separate `../genau` project — never opens `COM4`. It follows the broker-fed state, shows itself only in Genau mode, and reads clip/offset commands from `state/genau_cmd.txt`.

See those projects for the serial parsing, COM-port recovery, and clip-playback internals.

## Requirements

### Windows apps

- VLC
- MultiFunPlayer
- AutoHotkey v2
- `com0com`

### Python / tools

- Python (currently launched via Miniconda `pythonw.exe`)
- Python dependencies are declared in `pyproject.toml` — notably PyQt6 (dashboard), pygame-ce (audio companion), vosk + sounddevice (voice control), and Pillow / numpy / opencv-python.

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

- `C:\Program Files\MultiFunPlayer-1.34.2-patreon\MultiFunPlayer.config.json`

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

### MFP to primary VLC connection contract

Fun Time depends on MFP auto-connecting to the primary VLC instance, not one of the satellites.

The important runtime expectations are:

- the primary VLC HTTP interface comes up before MFP launches
- MFP starts before the portrait and landscape VLC instances
- MFP's saved VLC endpoint points at the configured primary VLC HTTP port
- Fun Time prefers the stable VLC HTTP password from `%APPDATA%\vlc\vlcrc` so MFP and the controller can agree on the same password across launches

If MFP stops loading scripts for the primary VLC, check:

- `C:\Program Files\MultiFunPlayer-1.34.2-patreon\MultiFunPlayer.config.json`
- `MediaSource.VLC.Endpoint` should match Fun Time's primary VLC port, currently `127.0.0.1:8090`
- `%APPDATA%\vlc\vlcrc` should contain the HTTP password that both VLC and MFP expect

## Hotkeys & voice

Fun Time is driven by global hotkeys and, optionally, spoken voice commands. While Fun Time is running and not OmniPaused, the hotkeys are global — they fire regardless of which window is focused.

The complete, always-current list of keys and spoken phrases lives in the app. Click the **?** button on the dashboard (tooltip "Hotkeys & Voice") to open the reference popup. It is generated directly from the source mappings below, so it can never drift from what the keys and voice grammar actually do:

- [`windows_bridge_hotkeys.ahk`](windows_bridge_hotkeys.ahk) — physical key → dispatch command
- [`fun_time/voice_commands.py`](fun_time/voice_commands.py) — spoken phrase → dispatch command
- [`fun_time/command_reference.py`](fun_time/command_reference.py) — joins both into the popup; `tests/test_command_reference.py` parses the AHK script and cross-checks the voice vocabulary so every real trigger stays represented

This README deliberately does not repeat the key table — open the **?** popup for it. The notes below cover the non-obvious behaviors that the table alone does not explain.

### Modes

The primary stack runs in one of three modes, each selected by its own hotkey (see the popup): **Genau**, **Hybrid**, and **VLC**. The `\` key is mode-dependent:

- in VLC mode, `\` opens the primary VLC open-file dialog. Fun Time enters OmniPause while the dialog is open and leaves it when the dialog closes, without toggling primary VLC playback.
- in Genau mode, `\` offsets Genau playback by a quarter cycle.

### OmniPause

- `Esc` toggles OmniPause; `Space` enters it.
- While OmniPaused, the global hotkeys are suspended — only `Esc` (toggle OmniPause) and `Ctrl+Alt+Q` (quit) stay active.

### F-Mode

Toggling F-Mode reloads all three VLC playlists immediately, rather than waiting for the next advance, and restricts each VLC to funscript-backed media:

- the primary VLC plays only videos that have a matching `.funscript` at the mirrored path, where `videos\videos\…` maps to `videos\scripts\scripts\….funscript`
- each satellite VLC plays only items that are in its normal portrait/landscape pool *and* listed in `favs.csv`

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

Written by the broker (the `../osr2_broker` project).

Values:

- `0` = VLC mode
- `1` = Genau mode

The audio companion and the Python dispatch loop both read this file as the authoritative source of whether Genau takeover is actually active.

### `genau_cmd.txt`

Written by `fun_time/command_dispatch.py` when Genau control commands are dispatched during Genau mode.

Values:

- `PREV`
- `NEXT`
- `OFFSET_QUARTER_CYCLE`

`OFFSET_QUARTER_CYCLE` advances Genau playback by one quarter of the current loop.

Genau (the `../genau` project) consumes and clears this file.

### Log files

The Python entry points write rotating logs in `state/`:

- `state/orchestrator.log`
- `state/windows_bridge.log`
- `state/genau_audio.log`

The broker and Genau write their own logs (e.g. `broker.log`, `genau_listener.log`, `genau_crash.log`) from the `../osr2_broker` and `../genau` projects.

## Notes on design

### Why the broker exists

Windows serial ports are effectively single-owner.

Without the broker:

- MFP and Genau would fight over `COM4`

With the broker:

- MFP uses virtual `COM14`
- broker uses `COM15` and real `COM4`
- Genau gets mode/timing info over localhost instead of serial

### Why Genau uses local files for commands/mode

For this setup, file-based signaling turned out to be a reliable and simple way to let:

- Python dispatch loop
- broker
- Genau

coordinate mode and clip-switch commands without depending on focused windows.

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

### MFP does not control the OSR2

Check:

- MFP is on the current `CNCA*` side of the `com0com` pair
- broker is running
- `com0com` pair exists
- broker log shows which broker-side port it chose
- OSR2 is still on `COM4`
- scheduled task `FunTime Genau Broker` is present and running (`Get-ScheduledTask -TaskName "FunTime Genau Broker"`)
- `C:\Program Files\MultiFunPlayer-1.34.2-patreon\MultiFunPlayer.config.json` does not still point at an old dead `CNCA*` device

### Genau never appears

Check:

- broker is running
- OSR2 is actually entering auto/free mode
- `state/genau_mode.txt` changes to `1`
- the broker's log (in `../osr2_broker`) for serial parsing / mode transitions
- Genau's log (in `../genau`) for UI/runtime errors

### Broker will not start

The broker is the `../osr2_broker` project — check its logs and config there. Also confirm:

- current serial ports still include the real OSR2 on `COM4`
- current serial ports still include a `com0com` pair

If the broker's config still points at an old broker-side port such as `COM15`, the broker tries to recover automatically by selecting the current `CNCB*` port.

If it still cannot recover, confirm that:

- MFP is on the matching `CNCA*` side
- the `com0com` pair actually exists in Windows Device Manager / serial-port enumeration

### `[` and `]` do not switch Genau clips

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

The broker, Genau, and Clipper are separate projects: `../osr2_broker`, `../genau`, `../clipper`.

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
