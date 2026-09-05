"""Fun Time's two icons follow the family's icon spec."""

from __future__ import annotations

from pathlib import Path

from shared_ui.app_icon import assert_follows_the_family_spec

import fun_time

PROJECT_DIR = Path(fun_time.__file__).resolve().parent.parent


def test_the_app_icon_is_the_familys_ft():
    # One PINK block letter on the family's 5x5 grid: Fun Time's two initials
    # sharing a stem.
    assert_follows_the_family_spec(PROJECT_DIR / "icon.ico", "FT")


def test_the_vr_icon_is_the_familys_v():
    assert_follows_the_family_spec(PROJECT_DIR / "vr_icon.ico", "V")
