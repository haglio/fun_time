"""Every command the reference and the recognizer can produce, dispatched in
every pairing of the two slots' modes, and what each one sent on every channel.
"""
from __future__ import annotations

import json
from pathlib import Path

from fun_time.bridge_records import BridgeConfig
from fun_time.command_dispatch import dispatch_command
from fun_time.command_reference import build_reference_sections
from fun_time.config import load_config
from fun_time.manifest import LaunchManifest, write_manifest_data, write_windows_bridge_manifest
from fun_time.mode_plan import MAIN_MODES
from fun_time.room_funestras import RoomFunestra
from fun_time.shared_state import BridgeState
from fun_time.voice_commands import VOICE_COMMANDS
from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest
from fun_time_vr.orchestrator import build_vr_manifest
from tests.made_up_genau import made_up_genau

# The channels a dispatch writes to.  The paused flags and the broker mailbox
# carry no vocabulary of their own — the headset and the broker read them
# exactly as the desktop does — so they are drained here to be seen by the
# sweep rather than checked verb by verb.
_CHANNELS = (
    "main_funestra_cmd_file", "genau_cmd_file", "portrait_cmd_file", "landscape_cmd_file",
    "origenerator_cmd_file", "broker_cmd_file", "main_funestra_paused_file",
    "genau_paused_file", "audio_paused_file", "portrait_paused_file",
    "landscape_paused_file", "origenerator_paused_file",
)


def headset_config(root: Path, *, names_an_origenerator: bool = True) -> BridgeConfig:
    """The bridge config a headset session runs on, built the way
    ``fun_time_vr.orchestrator.run_vr_bridge`` builds it: the VR manifest
    written out, read back, and handed to the dispatch loop's own builder."""
    config = room_config(root, names_an_origenerator=names_an_origenerator)
    manifest = write_manifest_data(build_vr_manifest(config),
                                   config.paths.state_dir / "windows_bridge_launch.ini")
    return build_bridge_config_from_manifest(LaunchManifest.read(manifest), vr_main_funestra=True)


def desktop_manifest(root: Path) -> LaunchManifest:
    """The manifest a session on the monitors runs on, as its orchestrator writes it."""
    config = room_config(root)
    return LaunchManifest.read(write_windows_bridge_manifest(
        config, config.paths.state_dir / "windows_bridge_launch.ini"))


def room_config(root: Path, *, names_an_origenerator: bool = True):
    """A fabricated config that names an Origenerator checkout, as a real one
    does, unless told not to."""
    folders = {name: root / name for name in (
        "primary", "portrait", "landscape", "vr", "weird", "state")}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    favs_file = root / "favs.csv"
    favs_file.write_text("local_file,web_url\n", encoding="utf-8")
    paths = {
        "ahk_exe": str(root / "AutoHotkey64.exe"),
        "python_exe": str(root / "python.exe"),
        "main_player_library_dirs": [str(folders["primary"])],
        "portrait_dirs": [str(folders["portrait"])],
        "landscape_dirs": [str(folders["landscape"])],
        "weird_dir": str(folders["weird"]),
        "genau_dir": str(made_up_genau(root)),
        "favs_file": str(favs_file),
        "state_dir": str(folders["state"]),
    }
    if names_an_origenerator:
        checkout = root / "origenerator"
        checkout.mkdir(exist_ok=True)
        paths["origenerator_dir"] = str(checkout)
    config_file = root / "fun_time_config.json"
    config_file.write_text(json.dumps({
        "paths": paths,
        "layout": {"primary_monitor": 1, "secondary_monitor": 2,
                   "main_top_ratio": 0.7, "landscape_width_ratio": 0.6},
        "audio_companion": {"host": "127.0.0.1", "port": 50556},
        "vr": {"library_dirs": [str(folders["vr"])]},
    }), encoding="utf-8")
    return load_config(config_file)


def every_command() -> list[str]:
    """Every dispatch id the reference and the recognizer can produce.

    The reference is held to the AHK script and to ``VOICE_COMMANDS`` by
    ``tests/test_command_reference.py``, so walking it here walks every key and
    every phrase without this module parsing either.
    """
    commands = {
        command
        for section in build_reference_sections()
        for row in section.rows
        for command in row.commands
    }
    return sorted(commands | set(VOICE_COMMANDS.values()))


def drain(config: BridgeConfig) -> dict[str, list[str]]:
    """What each channel was sent since the last drain, emptying them."""
    config.main_list_builds.settle()
    written: dict[str, list[str]] = {}
    for name in _CHANNELS:
        path = getattr(config, name, None)
        if path is None or not Path(path).exists():
            continue
        lines = [
            line.strip()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if lines:
            written[name] = lines
        Path(path).unlink()
    return written


def sweep(config: BridgeConfig, satellites_modes) -> dict[str, dict[str, list[str]]]:
    """Dispatch every command in every pairing of the two slots' modes; report
    what each sent.

    Keyed ``"<main mode>/<satellites mode>/<command>"``, because several
    controls route by mode and the padlock the headset's sweep was written for
    was dead in one and fine in the other — a failure has to say which.  The
    hosted app has always answered by the time a command is dispatched here, so
    switching into its mode is a switch rather than a notice that it is still
    starting.
    """
    drain(config)
    for status_file in (config.main_funestra_status_file,
                        *(config.satellite(funestra).status_file for funestra in RoomFunestra.SATELLITES)):
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(
            "video=C:/v/scene one.mp4\nduration_ms=3600000\nposition_ms=1800000\n",
            encoding="utf-8")
    landed: dict[str, dict[str, list[str]]] = {}
    for main_mode in MAIN_MODES:
        for satellites_mode in satellites_modes:
            for command in every_command():
                state = BridgeState(main_mode=main_mode, origenerator_ready=True)
                for funestra in RoomFunestra.SATELLITES:
                    state = state.with_satellite(funestra, mode=satellites_mode)
                _state, ops = dispatch_command(command, state, config, target_path="")
                written = drain(config)
                written["__ops__"] = sorted({op.op for op in ops})
                landed[f"{main_mode}/{satellites_mode}/{command}"] = written
    return landed
