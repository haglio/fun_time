from __future__ import annotations

from collections.abc import Collection
from typing import Any

from funestra.control_registry import Control, Verb, bind, look_up
from shared_ui.orientation import LANDSCAPE, ORIENTATIONS, PORTRAIT

from fun_time.inclusion_pairs import BOTH, Included

SET_GENAU_F_MODE = "SET_GENAU_F_MODE"
SET_GENAU_ORIENTATIONS = "SET_GENAU_ORIENTATIONS"


def genau_f_mode(on: bool) -> str:
    return f"{SET_GENAU_F_MODE} {int(on)}"


def orientation_words(included: Included) -> list[str]:
    return [word for word, kept in zip(ORIENTATIONS, included, strict=True) if kept]


def orientations_from(words: Collection[str] | None) -> Included:
    return BOTH if words is None else (PORTRAIT in words, LANDSCAPE in words)


def genau_orientations(included: Included) -> str:
    return " ".join((SET_GENAU_ORIENTATIONS, *orientation_words(included)))


def _set_f_mode(genau: Any, value: str) -> bool:
    genau.keep_favorites(value != "0")
    return True


def _set_orientations(genau: Any, value: str) -> bool:
    said = set(value.lower().split())
    if not said <= set(ORIENTATIONS):
        return False
    genau.keep_orientations(orientations_from(said))
    return True


VERBS = bind((
    Control(name="f_mode", verbs=(Verb(SET_GENAU_F_MODE, _set_f_mode, takes_a_value=True),)),
    Control(name="orientations",
            verbs=(Verb(SET_GENAU_ORIENTATIONS, _set_orientations, takes_a_value=True),)),
))


def apply_command(command: str, genau: Any) -> bool:
    return look_up(command, VERBS, genau)
