"""This repo's coverage floor. The settings and the gate are `app_support.coverage_gate`."""
from __future__ import annotations

from pathlib import Path

from app_support.coverage_gate import NotUnitTested, assert_config_is_the_familys

ROOT = Path(__file__).resolve().parent.parent

# What this repo says it does not unit-test, with the reason it gives. One place,
# never a pragma scattered through the tree.
NOT_UNIT_TESTED = (
    NotUnitTested("satellite/app.py", "a player's shell: needs the playback engine's library"),
    NotUnitTested("fun_time_vr/gl_contexts.py", "needs a graphics context"),
    NotUnitTested("fun_time_vr/player.py", "the VR player's shell: needs a headset"),
    NotUnitTested("fun_time_vr/render.py", "needs a graphics context"),
    NotUnitTested("fun_time_vr/video_thread.py", "needs a graphics context"),
    NotUnitTested("fun_time_vr/vr_session.py", "needs a headset and a graphics context"),
)


def test_the_coverage_config_is_the_familys():
    assert_config_is_the_familys(ROOT / ".coveragerc", NOT_UNIT_TESTED)
