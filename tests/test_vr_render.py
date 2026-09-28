"""fun_time_vr.render's platform-free seam: how a projection wraps the viewer.

The GL classes (RenderTarget, ScreenMesh, SceneRenderer) need a live context
and stay covered by the VR integration run; what a unit test CAN pin is the
projection-to-wrap mapping, which decides whether a clip wraps around the viewer
or hangs as a screen, and by which math — the difference the user watches.
"""
from __future__ import annotations

import ast
import inspect

from fun_time_vr import render
from fun_time_vr.projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_180_SBS,
    FISHEYE_190_SBS,
    FISHEYE_200_EQUISOLID_SBS,
    FISHEYE_200_STEREOGRAPHIC_SBS,
    FISHEYE_220_SBS,
    FLAT,
    MKX200_SBS,
    PROJECTIONS,
)
from fun_time_vr.render import (
    _CURVE_EQUIDISTANT,
    _CURVE_EQUISOLID,
    _CURVE_STEREOGRAPHIC,
    _FISHEYE_MODE,
    _IMMERSIVE_FRAGMENT_SHADER,
    immersive_wrap,
)

_FISHEYES = (FISHEYE_180_SBS, FISHEYE_190_SBS, MKX200_SBS, FISHEYE_220_SBS,
             FISHEYE_200_STEREOGRAPHIC_SBS, FISHEYE_200_EQUISOLID_SBS)


def test_every_projection_but_flat_wraps_the_viewer_its_own_way():
    wraps = {projection: immersive_wrap(projection)
             for projection in PROJECTIONS if projection != FLAT}

    assert None not in wraps.values(), "a wrap fell back to drawing as a screen"
    # Two drawn alike would show one of them with the other's mapping.
    assert len(set(wraps.values())) == len(wraps)


def test_a_flat_video_draws_as_a_screen_not_a_wrap():
    assert immersive_wrap(FLAT) is None


def test_an_unknown_projection_falls_back_to_the_screen():
    # The safe default: a projection this build has no shader for still shows
    # the video, just on a screen, instead of wrapping it wrongly or crashing.
    assert immersive_wrap("someday_projection") is None


class TestTheShaderAndTheTableAreOneSource:
    """The shader used to branch on the mode ids as literals, linked to the
    Python table only by a comment — and to derive each fisheye's field of view
    from the id, so renumbering the table silently changed what it drew."""

    def test_every_mode_id_but_the_else_arms_reaches_the_shader(self):
        modes = {immersive_wrap(projection).mode
                 for projection in PROJECTIONS if projection != FLAT}
        for mode in modes - {_FISHEYE_MODE}:
            assert f"mode == {mode}" in _IMMERSIVE_FRAGMENT_SHADER, mode

    def test_each_fisheye_is_drawn_at_the_angle_its_name_gives(self):
        for projection in _FISHEYES:
            degrees = immersive_wrap(projection).fov_deg
            assert str(int(degrees)) in projection, projection

    def test_the_fisheyes_are_exactly_the_projections_that_have_a_field_of_view(self):
        with_one = {projection for projection in PROJECTIONS
                    if projection != FLAT and immersive_wrap(projection).fov_deg}

        assert with_one == set(_FISHEYES)
        assert not immersive_wrap(EQUIRECT_180_SBS).fov_deg
        assert not immersive_wrap(EQUIRECT_360).fov_deg

    def test_the_shader_is_handed_the_angle_and_holds_none_of_its_own(self):
        assert "uniform float fov_half;" in _IMMERSIVE_FRAGMENT_SHADER
        for projection in _FISHEYES:
            assert str(immersive_wrap(projection).fov_deg) not in _IMMERSIVE_FRAGMENT_SHADER

    def test_every_glsl_brace_is_doubled_in_the_source(self):
        """It is an f-string, so a GLSL brace left single is an interpolation:
        `{` alone is a syntax error at import, but `{PI}` would be a NameError
        and `{0.0}` would silently render as `0.0` with the braces eaten.  The
        rendered text cannot show that, so this reads the source."""
        tree = ast.parse(inspect.getsource(render))
        node = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and getattr(n.targets[0], "id", "") == "_IMMERSIVE_FRAGMENT_SHADER")
        literal = "".join(
            part.value for part in node.value.values  # type: ignore[attr-defined]
            if isinstance(part, ast.Constant))

        # Every brace that survived as text; the interpolations are the mode
        # and curve ids, none of which carries one.
        assert literal.count("{") == literal.count("}") == 9
        assert render._IMMERSIVE_FRAGMENT_SHADER.count("{") == 9


class TestTheFisheyeCurvesBesideTheAngle:
    """iZugar markets the MKX200/220 as APO-corrected against the plain
    equidistant mapping every other fisheye entry here draws with, and the
    tools that master this footage list equidistant, equisolid and
    stereographic as different curves, not different fields of view.  Cycling
    the existing entries only changes degrees, so a video mastered on either
    of the other two curves stays pinched at its edge whatever degree is tried."""

    def test_the_new_curves_hold_mkx200s_own_angle(self):
        for projection in (FISHEYE_200_STEREOGRAPHIC_SBS, FISHEYE_200_EQUISOLID_SBS):
            assert (immersive_wrap(projection).fov_deg
                    == immersive_wrap(MKX200_SBS).fov_deg)

    def test_each_entry_is_marked_for_the_curve_it_draws(self):
        assert immersive_wrap(MKX200_SBS).curve == _CURVE_EQUIDISTANT
        assert immersive_wrap(FISHEYE_200_STEREOGRAPHIC_SBS).curve == _CURVE_STEREOGRAPHIC
        assert immersive_wrap(FISHEYE_200_EQUISOLID_SBS).curve == _CURVE_EQUISOLID

    def test_the_shader_computes_the_curves_the_optics_define(self):
        assert "uniform int curve;" in _IMMERSIVE_FRAGMENT_SHADER
        assert f"curve == {_CURVE_STEREOGRAPHIC}" in _IMMERSIVE_FRAGMENT_SHADER
        assert f"curve == {_CURVE_EQUISOLID}" in _IMMERSIVE_FRAGMENT_SHADER
        assert "tan(off_axis" in _IMMERSIVE_FRAGMENT_SHADER
        assert "sin(off_axis" in _IMMERSIVE_FRAGMENT_SHADER
