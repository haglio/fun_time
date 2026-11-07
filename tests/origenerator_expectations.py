from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fun_time.bridge_records import BridgeConfig
from fun_time.command_dispatch import dispatch_command
from fun_time.command_value import FILTER, parse_command, set_command
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.hosted_contract import SAID_AT_FIELD, said_to_the_hosted_app
from fun_time.hosted_origenerator import the_session_it_is_handed, what_an_adopted_app_is_told
from fun_time.hud_feed import HudFeed
from fun_time.hud_transport import HudPublisher
from fun_time.lock import go_to_line
from fun_time.manifest import LaunchManifest
from fun_time.origenerator_switches import (
    KINO_MODE,
    ORIGENERATOR_MODE,
    SATELLITE_MODE_COMMANDS,
    origenerator_shows,
)
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.window_names import ORIGENERATOR_TITLE
from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest
from fun_time.windows_bridge_orchestrator import ChildProcess, see_the_hosted_app_out
from fun_time.windows_bridge_startup import ORIGENERATOR_MODULE, origenerator_session_args
from fun_time_vr.gallery_panel import HOSTED_RELEASE, HOVER, SCROLL
from fun_time_vr.pointer import DRAG, PRESS, RIGHT_CLICK
from tests.dispatch_sweep import desktop_manifest, drain, headset_config, sweep
from tests.hosted_shows import publish_the_show

EXPECTATIONS = Path(__file__).with_suffix(".json")


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
        "said_at_field": SAID_AT_FIELD,
    }


_A_RECT = WindowRect(0, 0, 10, 10)
_A_PLAN = WindowLayoutPlan(portrait=_A_RECT, landscape=_A_RECT, dashboard=_A_RECT,
                           random_favs_browser=_A_RECT)


def _the_launches(manifest: LaunchManifest) -> dict[str, list[str]]:
    return {
        where: sorted(word for word in origenerator_session_args(
            the_session_it_is_handed(manifest, _A_PLAN, in_a_headset=in_a_headset))
            if word.startswith("--"))
        for where, in_a_headset in (("on the monitors", False), ("in a headset", True))
    }


def _the_lines_it_writes(on_the_monitors: BridgeConfig, in_a_headset: BridgeConfig) -> set[str]:
    written = [
        *_written_in_a_sweep(on_the_monitors),
        *_written_in_a_sweep(in_a_headset),
        *_written_by(on_the_monitors, _each_satellites_own_mode_buttons, _the_apps_f_mode,
                     _each_hosted_satellite_told_whether_it_has_the_osr2,
                     _a_taken_over_copy_handed_back,
                     _a_copy_parked_for_the_app_crossing_in),
        *(what_an_adopted_app_is_told("{frames}", "{input}", in_a_headset=in_a_headset)
          for in_a_headset in (False, True)),
        *(said_to_the_hosted_app(funestra.label, "{words}")
          for funestra in FunTimeFunestra.SATELLITES),
    ]
    return {_as_published(line) for line in written}


def _as_published(line: str) -> str:
    if line.startswith(go_to_line("")):
        return go_to_line("{file}")
    said = parse_command(line)
    return set_command(said.said_to, "{row}") if said.action == FILTER else line


def _written_in_a_sweep(app: BridgeConfig) -> list[str]:
    return [line for written in sweep(app, (KINO_MODE, ORIGENERATOR_MODE)).values()
            for line in written.get("origenerator_cmd_file", ())]


def _written_by(app: BridgeConfig, *acts) -> list[str]:
    written = []
    for act in acts:
        act(app)
        written += drain(app).get("origenerator_cmd_file", [])
    return written


def _each_satellites_own_mode_buttons(app: BridgeConfig) -> None:
    for command, (funestra, mode) in SATELLITE_MODE_COMMANDS.items():
        away = KINO_MODE if origenerator_shows(mode) else ORIGENERATOR_MODE
        dispatch_command(command, BridgeState(origenerator_ready=True).with_satellite(
            funestra, mode=away), app)


def _both_shows_hosted() -> BridgeState:
    hosted = SatelliteState(mode=ORIGENERATOR_MODE, handed_over=True)
    return BridgeState(portrait=hosted, landscape=hosted, origenerator_ready=True)


def _each_hosted_satellite_told_whether_it_has_the_osr2(app: BridgeConfig) -> None:
    feed = HudFeed(config=app, publisher=HudPublisher({}, app.state_dir / "thumbs"))
    for funestra in FunTimeFunestra.SATELLITES:
        feed.publish(replace(_both_shows_hosted(), osr2_funestra=funestra.value))


def _the_apps_f_mode(app: BridgeConfig) -> None:
    for funestra in FunTimeFunestra.SATELLITES:
        publish_the_show(app, funestra, f_mode=False)
    with patch("fun_time.f_mode.apply_fmode",
               return_value=SimpleNamespace(funestras=(), log_message="")):
        dispatch_command("fmode_toggle", _both_shows_hosted(), app)


def _a_taken_over_copy_handed_back(app: BridgeConfig) -> None:
    see_the_hosted_app_out(app.state_dir, None, app.origenerator_cmd_file,
                           keep=False, taken_over=True)


def _a_copy_parked_for_the_app_crossing_in(app: BridgeConfig) -> None:
    with patch("fun_time.windows_bridge_orchestrator.find_origenerators_window", return_value=1), \
         patch("fun_time.windows_bridge_orchestrator.hide_window"):
        see_the_hosted_app_out(app.state_dir, ChildProcess(pid=1, created_at=1),
                               app.origenerator_cmd_file, keep=True, taken_over=False)
