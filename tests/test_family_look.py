"""Fun Time wears the family's look: shared_ui's colors and marks, none of its own."""
from __future__ import annotations

from pathlib import Path

from shared_ui.look_guard import assert_the_look_is_shared_uis

_CHECKOUT = Path(__file__).resolve().parents[1]


def test_every_color_and_mark_fun_time_draws_comes_from_shared_ui():
    assert_the_look_is_shared_uis(_CHECKOUT, [
        _CHECKOUT / package for package in ("fun_time", "main_funestra", "satellite", "fun_time_vr", "tools")])
