from __future__ import annotations

from pathlib import Path

from funestra import file_channel
from funestra.file_channel import publish_whole
from funestra.hud_button import Button
from funestra.hud_controls import reset_button
from funestra.hud_icons import FMODE_ICON
from funestra.hud_title import F_MODE_LABEL
from funestra.satellite_hud import HudModel, hud_text

from fun_time.bridge_records import BridgeConfig
from fun_time.config import ProjectConfig
from fun_time.fun_time_funestras import FunTimeFunestra


def take_the_satellite(config: BridgeConfig | ProjectConfig, funestra: FunTimeFunestra) -> None:
    publish_whole(config.satellite(funestra).origenerator_hud_file,
                  hud_text(HudModel(funestra=funestra.label, title="Stub slideshow")))


def an_app_that_opens_each_slideshow_asked_of_it(config: BridgeConfig | ProjectConfig, heard: list[str]):
    def hear(_path, verb: str) -> None:
        heard.append(verb)
        for funestra in FunTimeFunestra.SATELLITES:
            if verb == f"{funestra.label}_open_slideshow":
                take_the_satellite(config, funestra)

    return hear


def an_origenerator_that_answers(config: ProjectConfig, monkeypatch) -> list[str]:
    config.origenerator_status_file.parent.mkdir(parents=True, exist_ok=True)
    config.origenerator_status_file.write_text("paused = 0\n", encoding="utf-8")
    heard: list[str] = []
    the_app = an_app_that_opens_each_slideshow_asked_of_it(config, heard)
    append_for_real = file_channel.append_command

    def append(path, verb: str) -> None:
        append_for_real(path, verb)
        if Path(path) == config.origenerator_cmd_file:
            the_app(path, verb)

    monkeypatch.setattr("fun_time.runtime_flow.append_command", append)
    return heard


def publish_the_show(config: BridgeConfig, funestra: FunTimeFunestra, *, f_mode: bool = False,
                     enhanced: bool | None = None, nothing_to_reset: bool = True) -> None:
    buttons = [Button(f"{funestra.label}_fmode", FMODE_ICON, F_MODE_LABEL, lit=f_mode),
               reset_button(funestra.label, nothing_to_reset=nothing_to_reset, tooltip="Reset")]
    if enhanced is not None:
        buttons.append(Button(f"{funestra.label}_enhanced", "E", "Enhanced only", lit=enhanced))
    hud = HudModel(funestra=funestra.label, rows=(tuple(buttons),))
    config.satellite(funestra).origenerator_hud_file.write_text(hud_text(hud), encoding="utf-8")
