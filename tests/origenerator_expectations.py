"""What Fun Time relies on of the Origenerator it hosts, read off the code that
relies on it: the module it starts, the caption it finds the window by, the
flags of each launch, the words a headset writes on the app's input file and
every line a room writes on its command file.

Origenerator's suite reads the document from this repo's main before that app
ships anything, and tests/test_origenerator_expectations.py holds it here to
this code and to what Origenerator has shipped -- so a change on either side
that would cost a room its Origenerator fails in the suite of the app that made
it.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fun_time.bridge_records import BridgeConfig
from fun_time.command_dispatch import dispatch_command, said_to_the_hosted_app
from fun_time.filter_vocab import decode_filter_command, set_command
from fun_time.hosted_origenerator import the_session_it_is_handed, what_an_adopted_app_is_told
from fun_time.lock import go_to_line
from fun_time.manifest import LaunchManifest
from fun_time.origenerator_switches import (
    KINO_MODE,
    ORIGENERATOR_MODE,
    SATELLITE_MODE_COMMANDS,
    origenerator_shows,
)
from fun_time.room_funestras import RoomFunestra
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.window_roles import ORIGENERATOR_TITLE
from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest
from fun_time.windows_bridge_orchestrator import ChildProcess, see_the_hosted_app_out
from fun_time.windows_bridge_startup import ORIGENERATOR_MODULE, origenerator_session_args
from fun_time_vr.gallery_panel import HOSTED_RELEASE, HOVER, SCROLL
from fun_time_vr.pointer import DRAG, PRESS, RIGHT_CLICK
from tests.dispatch_sweep import desktop_manifest, drain, headset_config, sweep
from tests.hosted_shows import publish_the_show

EXPECTATIONS = Path(__file__).with_suffix(".json")

# Said to a side in Origenerator mode, where what a side is told is the hosted
# app's to answer, and answered by nothing over there.  Keyed by what the line
# says after the side.
LEFT_UNANSWERED: dict[str, str] = {
    "wrong_action": "a show's image carries no act label to strike, and the app "
                    "refuses the strike on purpose",
    "nudge_next": "a show holds each image or video for as long as its own pace "
                  "says, and has no ten-second step through one",
    "nudge_prev": "a show holds each image or video for as long as its own pace "
                  "says, and has no ten-second step through one",
}


def published_text(scratch: Path) -> str:
    return json.dumps(expectations(scratch), indent=2) + "\n"


def expectations(scratch: Path) -> dict:
    on_the_monitors = desktop_manifest(scratch / "on_the_monitors")
    return {
        "module": ORIGENERATOR_MODULE,
        "window_title": ORIGENERATOR_TITLE,
        "launches": _the_launches(on_the_monitors),
        "headset_words": sorted({PRESS, HOSTED_RELEASE, DRAG, HOVER, SCROLL, RIGHT_CLICK}),
        "command_lines": sorted(_the_lines_it_writes(
            build_bridge_config_from_manifest(on_the_monitors), headset_config(scratch / "in_a_headset"))),
    }


def _said_after_the_side(line: str) -> str:
    for funestra in RoomFunestra.SATELLITES:
        side = f"{funestra.label}_"
        if line.lower().startswith(side):
            return line[len(side):].lower()
    return line


_A_RECT = WindowRect(0, 0, 10, 10)
_A_PLAN = WindowLayoutPlan(portrait=_A_RECT, landscape=_A_RECT, dashboard=_A_RECT,
                           random_favs_browser=_A_RECT)


def _the_launches(manifest: LaunchManifest) -> dict[str, list[str]]:
    return {
        where: sorted(word for word in origenerator_session_args(
            **the_session_it_is_handed(manifest, _A_PLAN, in_a_headset=in_a_headset))
            if word.startswith("--"))
        for where, in_a_headset in (("on the monitors", False), ("in a headset", True))
    }


def _the_lines_it_writes(on_the_monitors: BridgeConfig, in_a_headset: BridgeConfig) -> set[str]:
    written = [
        *_written_in_a_sweep(on_the_monitors),
        *_written_in_a_sweep(in_a_headset),
        *_written_by(on_the_monitors, _each_satellites_own_mode_buttons, _the_rooms_f_mode,
                     _a_taken_over_copy_handed_back,
                     _a_copy_parked_for_the_room_crossing_in),
        *(what_an_adopted_app_is_told("{frames}", "{input}", in_a_headset=in_a_headset)
          for in_a_headset in (False, True)),
        *(said_to_the_hosted_app(funestra.label, "{words}")
          for funestra in RoomFunestra.SATELLITES),
    ]
    return {_as_published(line) for line in written
            if _said_after_the_side(line) not in LEFT_UNANSWERED}


def _as_published(line: str) -> str:
    """A filter's row is an act of his content overlay, and a go-to's file is a
    video of his library, neither of which this document carries."""
    if line.startswith(go_to_line("")):
        return go_to_line("{file}")
    filtered = decode_filter_command(line)
    return line if filtered is None else set_command(filtered[0], "{row}")


def _written_in_a_sweep(room: BridgeConfig) -> list[str]:
    return [line for written in sweep(room, (KINO_MODE, ORIGENERATOR_MODE)).values()
            for line in written.get("origenerator_cmd_file", ())]


def _written_by(room: BridgeConfig, *acts) -> list[str]:
    written = []
    for act in acts:
        act(room)
        written += drain(room).get("origenerator_cmd_file", [])
    return written


def _each_satellites_own_mode_buttons(room: BridgeConfig) -> None:
    for command, (funestra, mode) in SATELLITE_MODE_COMMANDS.items():
        away = KINO_MODE if origenerator_shows(mode) else ORIGENERATOR_MODE
        dispatch_command(command, BridgeState(origenerator_ready=True).with_satellite(
            funestra, mode=away), room)


def _both_shows_hosted() -> BridgeState:
    hosted = SatelliteState(mode=ORIGENERATOR_MODE)
    return BridgeState(portrait=hosted, landscape=hosted, origenerator_ready=True)


def _the_rooms_f_mode(room: BridgeConfig) -> None:
    for funestra in RoomFunestra.SATELLITES:
        publish_the_show(room, funestra, f_mode=False)
    with patch("fun_time.command_dispatch.apply_fmode",
               return_value=SimpleNamespace(funestras=(), log_message="")):
        dispatch_command("fmode_toggle", _both_shows_hosted(), room)


def _a_taken_over_copy_handed_back(room: BridgeConfig) -> None:
    see_the_hosted_app_out(room.state_dir, None, room.origenerator_cmd_file,
                           keep=False, taken_over=True)


def _a_copy_parked_for_the_room_crossing_in(room: BridgeConfig) -> None:
    with patch("fun_time.windows_bridge_orchestrator.find_origenerators_window", return_value=1), \
         patch("fun_time.windows_bridge_orchestrator.hide_window"):
        see_the_hosted_app_out(room.state_dir, ChildProcess(pid=1, created_at=1),
                               room.origenerator_cmd_file, keep=True, taken_over=False)
