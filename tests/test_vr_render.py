"""fun_time_vr.render's platform-free seam: which shader wraps a projection.

The GL classes (RenderTarget, ScreenMesh, SceneRenderer) need a live context
and stay covered by the VR integration run; what a unit test CAN pin is the
projection-to-shader-mode mapping, which decides whether a clip wraps around
the viewer or hangs as a screen — the difference the user watches.
"""
from __future__ import annotations

from fun_time_vr.projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_190_SBS,
    FLAT,
    MKX200_SBS,
)
from fun_time_vr.render import immersive_mode


def test_every_wrapped_projection_gets_its_own_shader_mode():
    modes = {
        projection: immersive_mode(projection)
        for projection in (EQUIRECT_180_SBS, FISHEYE_190_SBS, MKX200_SBS, EQUIRECT_360)
    }

    assert None not in modes.values(), "a wrap fell back to drawing as a screen"
    # Distinct wraps run distinct shader math; two sharing a mode would render
    # one of them with the other's mapping.
    assert len(set(modes.values())) == len(modes)


def test_a_flat_video_draws_as_a_curved_screen_not_a_wrap():
    assert immersive_mode(FLAT) is None


def test_an_unknown_projection_falls_back_to_the_screen():
    # The safe default: a projection this build has no shader for still shows
    # the video, just on a screen, instead of wrapping it wrongly or crashing.
    assert immersive_mode("someday_projection") is None
