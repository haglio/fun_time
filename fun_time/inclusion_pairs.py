"""The Main Funestra's pairs of inclusion buttons: two kinds of video each, a
button for each kind, lit while that kind may play."""
from __future__ import annotations

from dataclasses import dataclass

from funestra.hud_button import Button
from funestra.hud_marks import shared_mark
from funestra.modes import LengthMode
from shared_ui.orientation import LANDSCAPE, PORTRAIT

from .funestra_buttons import lit_or_remembered

Included = tuple[bool, bool]
BOTH: Included = (True, True)


@dataclass(frozen=True)
class Kind:
    word: str
    mark: str
    named: str

    def name(self, things: str) -> str:
        return self.named.format(things=things)


@dataclass(frozen=True, eq=False)
class InclusionPair:
    first: Kind
    second: Kind
    commands: dict[Included, str]

    @property
    def asked(self) -> dict[str, Included]:
        return {command: included for included, command in self.commands.items()}

    def buttons(self, included: Included, *, remembered: bool,
                things: str = "videos") -> tuple[Button, Button]:
        first, second = included
        return (
            _inclusion_button(self.commands[(not first, second)], self.first, things,
                              on=first, remembered=remembered, group_break=True),
            _inclusion_button(self.commands[(first, not second)], self.second, things,
                              on=second, remembered=remembered),
        )

    def flash(self, included: Included) -> str:
        if included == BOTH:
            return f"{self.first.word} + {self.second.word}"
        kept = self._the_one_kept(included)
        return f"{kept.word} only" if kept else ""

    def title_word(self, included: Included) -> str:
        kept = self._the_one_kept(included)
        return kept.word if kept else ""

    def _the_one_kept(self, included: Included) -> Kind | None:
        first, second = included
        if first == second:
            return None
        return self.first if first else self.second


def _inclusion_button(command: str, kind: Kind, things: str, *, on: bool,
                      remembered: bool, group_break: bool = False) -> Button:
    named = kind.name(things)
    tooltip = f"Including {named}" if on else f"Not including {named}"
    return Button(command, kind.mark, tooltip, group_break=group_break,
                  **lit_or_remembered(on, remembered))


PROJECTION = InclusionPair(
    Kind("2D", shared_mark("flat_2d"), "2D {things}"),
    Kind("VR", shared_mark("vr_hemisphere"), "VR {things}"),
    {(True, True): "main_projection_both", (True, False): "main_projection_flat",
     (False, True): "main_projection_vr", (False, False): "main_projection_none"},
)

LENGTH = InclusionPair(
    Kind("Clips", shared_mark("clips"), "clips"),
    Kind("Full", shared_mark("full"), "full videos"),
    {(True, True): "main_player_length_mixed", (True, False): "main_player_length_clips",
     (False, True): "main_player_length_full", (False, False): "main_player_length_none"},
)

ORIENTATION = InclusionPair(
    Kind("Portrait", shared_mark(PORTRAIT), "portrait {things}"),
    Kind("Landscape", shared_mark(LANDSCAPE), "landscape {things}"),
    {(True, True): "main_orientation_both", (True, False): "main_orientation_portrait",
     (False, True): "main_orientation_landscape", (False, False): "main_orientation_none"},
)

_LENGTHS: dict[Included, LengthMode] = {
    (True, True): LengthMode.MIXED, (True, False): LengthMode.CLIPS,
    (False, True): LengthMode.FULL, (False, False): LengthMode.NONE,
}


def length_mode(included: Included) -> LengthMode:
    return _LENGTHS[included]


def lengths_included(length: LengthMode) -> Included:
    return next(included for included, mode in _LENGTHS.items() if mode is length)
