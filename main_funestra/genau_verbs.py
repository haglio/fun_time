from __future__ import annotations

from typing import Any

from funestra.control_registry import Control, Verb, bind, look_up

SET_GENAU_F_MODE = "SET_GENAU_F_MODE"


def genau_f_mode(on: bool) -> str:
    return f"{SET_GENAU_F_MODE} {int(on)}"


def _set_f_mode(genau: Any, value: str) -> bool:
    genau.keep_favorites(value != "0")
    return True


VERBS = bind((
    Control(name="f_mode", verbs=(Verb(SET_GENAU_F_MODE, _set_f_mode, takes_a_value=True),)),
))


def apply_command(command: str, genau: Any) -> bool:
    return look_up(command, VERBS, genau)
