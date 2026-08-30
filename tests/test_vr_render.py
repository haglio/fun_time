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


class TestTheShaderAndTheTableAreOneSource:
    """The shader used to branch on the mode ids as literals, linked to the
    Python table only by a comment — and to derive each fisheye's field of view
    from the id, so renumbering the table silently changed what it drew."""

    def test_every_mode_id_reaches_the_shader(self):
        from fun_time_vr.render import _IMMERSIVE_FRAGMENT_SHADER, _PROJECTION_MODES

        for projection, mode in _PROJECTION_MODES.items():
            if projection is MKX200_SBS:
                continue  # the else arm; it is not compared against
            assert f"mode == {mode}" in _IMMERSIVE_FRAGMENT_SHADER, projection

    def test_each_fisheye_is_drawn_at_the_angle_its_name_gives(self):
        """`fisheye_190_sbs` is 190 degrees and `mkx200_sbs` is 200.  The shader
        read those off the mode id, which is not what either name says."""
        from fun_time_vr.render import _FISHEYE_FOV_DEGREES

        for projection, degrees in _FISHEYE_FOV_DEGREES.items():
            assert str(int(degrees)) in projection, projection

    def test_the_fisheyes_are_exactly_the_projections_that_have_a_field_of_view(self):
        from fun_time_vr.render import _FISHEYE_FOV_DEGREES, _PROJECTION_MODES

        assert set(_FISHEYE_FOV_DEGREES) == {FISHEYE_190_SBS, MKX200_SBS}
        assert set(_FISHEYE_FOV_DEGREES) <= set(_PROJECTION_MODES)

    def test_both_of_those_angles_are_written_into_the_shader(self):
        from fun_time_vr.render import _FISHEYE_FOV_DEGREES, _IMMERSIVE_FRAGMENT_SHADER

        for degrees in _FISHEYE_FOV_DEGREES.values():
            assert str(degrees) in _IMMERSIVE_FRAGMENT_SHADER

    def test_every_glsl_brace_is_doubled_in_the_source(self):
        """It is an f-string, so a GLSL brace left single is an interpolation:
        `{` alone is a syntax error at import, but `{PI}` would be a NameError
        and `{0.0}` would silently render as `0.0` with the braces eaten.  The
        rendered text cannot show that, so this reads the source."""
        import ast
        import inspect

        from fun_time_vr import render

        tree = ast.parse(inspect.getsource(render))
        node = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and getattr(n.targets[0], "id", "") == "_IMMERSIVE_FRAGMENT_SHADER")
        literal = "".join(
            part.value for part in node.value.values  # type: ignore[attr-defined]
            if isinstance(part, ast.Constant))

        # Every brace that survived as text; the interpolations are the mode
        # ids and the two fields of view, none of which carries one.
        assert literal.count("{") == literal.count("}") == 6
        assert render._IMMERSIVE_FRAGMENT_SHADER.count("{") == 6
