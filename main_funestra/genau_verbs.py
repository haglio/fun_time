from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from funestra.control_registry import Control, Verb, bind, look_up
from funestra.inclusion_pair import BOTH, Included
from shared_ui.orientation import LANDSCAPE, ORIENTATIONS, PORTRAIT

SET_GENAU_FAVORITES = "SET_GENAU_FAVORITES"
SET_GENAU_ENHANCED = "SET_GENAU_ENHANCED"
SET_GENAU_ORIENTATIONS = "SET_GENAU_ORIENTATIONS"


def genau_favorites(on: bool) -> str:
    return f"{SET_GENAU_FAVORITES} {int(on)}"


def genau_enhanced(on: bool) -> str:
    return f"{SET_GENAU_ENHANCED} {int(on)}"


def orientation_words(included: Included) -> list[str]:
    return [word for word, kept in zip(ORIENTATIONS, included, strict=True) if kept]


def orientations_from(words: Collection[str] | None) -> Included:
    return BOTH if words is None else (PORTRAIT in words, LANDSCAPE in words)


def genau_orientations(included: Included) -> str:
    return " ".join((SET_GENAU_ORIENTATIONS, *orientation_words(included)))


def _switched_by(keep: Callable[[Any], Callable[[bool], None]]) -> Callable[[Any, str], bool]:
    def switch(genau: Any, value: str) -> bool:
        keep(genau)(value != "0")
        return True
    return switch


def _set_orientations(genau: Any, value: str) -> bool:
    said = set(value.lower().split())
    if not said <= set(ORIENTATIONS):
        return False
    genau.keep_orientations(orientations_from(said))
    return True


VERBS = bind((
    Control(name="favorites",
            verbs=(Verb(SET_GENAU_FAVORITES, _switched_by(lambda genau: genau.keep_favorites), takes_a_value=True),)),
    Control(name="enhanced",
            verbs=(Verb(SET_GENAU_ENHANCED, _switched_by(lambda genau: genau.keep_enhanced), takes_a_value=True),)),
    Control(name="orientations",
            verbs=(Verb(SET_GENAU_ORIENTATIONS, _set_orientations, takes_a_value=True),)),
))


def apply_command(command: str, genau: Any) -> bool:
    return look_up(command, VERBS, genau)
