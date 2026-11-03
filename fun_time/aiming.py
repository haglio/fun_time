from __future__ import annotations

from .event_log import SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT, SOURCE_SYSTEM
from .fun_time_funestras import FunTimeFunestra

MAIN_EQUIVALENTS = {
    "next": "main_next",
    "prev": "main_prev",
    "nudge_next": "main_nudge_next",
    "nudge_prev": "main_nudge_prev",
    "funscript_jump": "main_funestra_funscript_jump",
    "next_funscripted": "main_funestra_next_funscripted",
    "no_loop": "main_funestra_loop_cancel",
    "lock_on": "main_lock_on",
    "lock_off": "main_lock_off",
    "fmode": "main_fmode",
    "fmode_on": "main_fmode_on",
    "fmode_off": "main_fmode_off",
    "reset": "main_reset",
    "trash": "genau_trash_flick",
    "latest": "main_latest",
    "shuffle": "main_shuffle",
    "hud_minimize": "main_hud_minimize",
    "hud_restore": "main_hud_restore",
}


def named_first_or_last(funestra_word: str, words: str, command: str) -> dict[str, str]:
    return dict.fromkeys((f"{funestra_word} {words}", f"{words} {funestra_word}"), command)


def on_the_main_funestra(action: str) -> str | None:
    if action.startswith("speed_"):
        return f"main_funestra_{action}"
    return MAIN_EQUIVALENTS.get(action)


def resolve_active_funestra_command(command: str, active_funestra: int) -> str:
    if not command.startswith("active_"):
        return command
    action = command.removeprefix("active_")
    if active_funestra == FunTimeFunestra.MAIN:
        return on_the_main_funestra(action) or command
    satellite = "portrait" if active_funestra == FunTimeFunestra.PORTRAIT else "landscape"
    return f"{satellite}_{action}"


_NAMED_BY = {"portrait_": FunTimeFunestra.PORTRAIT, "landscape_": FunTimeFunestra.LANDSCAPE,
             "main_": FunTimeFunestra.MAIN, "genau_": FunTimeFunestra.MAIN}


def command_funestra(command: str) -> FunTimeFunestra | None:
    return next((funestra for prefix, funestra in _NAMED_BY.items()
                 if command.startswith(prefix)), None)


FUNESTRA_SOURCES = {
    FunTimeFunestra.MAIN: SOURCE_MAIN,
    FunTimeFunestra.PORTRAIT: SOURCE_PORTRAIT,
    FunTimeFunestra.LANDSCAPE: SOURCE_LANDSCAPE,
}


def message_source(command: str, active_funestra: int | None) -> str:
    funestra = active_funestra if command.startswith("active_") else command_funestra(command)
    return FUNESTRA_SOURCES.get(funestra, SOURCE_SYSTEM)

