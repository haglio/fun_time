from __future__ import annotations

from funestra.hud_icons import shared_icon
from funestra.inclusion_pair import Included, InclusionPair, Kind
from funestra.modes import LengthMode
from shared_ui.orientation import LANDSCAPE, PORTRAIT

PROJECTION = InclusionPair(
    Kind("2D", shared_icon("flat_2d"), "2D {things}"),
    Kind("VR", shared_icon("vr_hemisphere"), "VR {things}"),
    {(True, True): "main_projection_both", (True, False): "main_projection_flat",
     (False, True): "main_projection_vr", (False, False): "main_projection_none"},
)

LENGTH = InclusionPair(
    Kind("Clips", shared_icon("clips"), "clips"),
    Kind("Full", shared_icon("full"), "full videos"),
    {(True, True): "main_funestra_length_mixed", (True, False): "main_funestra_length_clips",
     (False, True): "main_funestra_length_full", (False, False): "main_funestra_length_none"},
)

ORIENTATION = InclusionPair(
    Kind("Portrait", shared_icon(PORTRAIT), "portrait {things}"),
    Kind("Landscape", shared_icon(LANDSCAPE), "landscape {things}"),
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
