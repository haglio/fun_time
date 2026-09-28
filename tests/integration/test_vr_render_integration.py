from __future__ import annotations

import math
import os
import sys

import glfw
import numpy as np
import pytest
from OpenGL import GL

from fun_time_vr.gl_contexts import hidden_gl_window
from fun_time_vr.matrices import fov_to_projection_matrix
from fun_time_vr.projection import (
    FISHEYE_200_EQUISOLID_SBS,
    FISHEYE_200_STEREOGRAPHIC_SBS,
    MKX200_SBS,
)
from fun_time_vr.render import FrameTexture, RenderTarget, SceneRenderer, immersive_wrap

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

DRAWN_PX = 128
SOURCE_EYE_PX = 128
HALF_FOV = math.radians(200) / 2
LOOKS_OUT_TO = math.radians(80)
CLOSE_ENOUGH = 0.02
AT_LEAST_THIS_DIFFERENT = 0.02

RADIUS_READ_AT = {
    MKX200_SBS: lambda off_axis: off_axis / HALF_FOV,
    FISHEYE_200_STEREOGRAPHIC_SBS: lambda off_axis: np.tan(off_axis / 2) / math.tan(HALF_FOV / 2),
    FISHEYE_200_EQUISOLID_SBS: lambda off_axis: np.sin(off_axis / 2) / math.sin(HALF_FOV / 2),
}


@pytest.fixture
def renderer():
    assert glfw.init(), "glfw failed to initialize"
    window = hidden_gl_window("vr-render-test")
    glfw.make_context_current(window)
    drawing = SceneRenderer()
    yield drawing
    drawing.close()
    glfw.destroy_window(window)


def _side_by_side_picture_whose_red_is_the_distance_from_each_eyes_center():
    ys, xs = np.mgrid[0:SOURCE_EYE_PX, 0:SOURCE_EYE_PX]
    distance = np.hypot((xs + 0.5) / SOURCE_EYE_PX - 0.5, (ys + 0.5) / SOURCE_EYE_PX - 0.5) / 0.5
    eye = np.zeros((SOURCE_EYE_PX, SOURCE_EYE_PX, 4), np.uint8)
    eye[..., 0] = (np.clip(distance, 0, 1) * 255).astype(np.uint8)
    eye[..., 3] = 255
    return np.concatenate([eye, eye], axis=1)


def _inverse_of_looking_straight_ahead():
    projection = fov_to_projection_matrix(
        -LOOKS_OUT_TO, LOOKS_OUT_TO, LOOKS_OUT_TO, -LOOKS_OUT_TO, 0.05, 100.0)
    return np.linalg.inv(projection).astype(np.float32)


def _angle_off_axis_at_each_drawn_pixel(inverse):
    ys, xs = np.mgrid[0:DRAWN_PX, 0:DRAWN_PX]
    screen = np.stack([
        (xs + 0.5) / DRAWN_PX * 2 - 1, (ys + 0.5) / DRAWN_PX * 2 - 1,
        -np.ones(xs.shape), np.ones(xs.shape),
    ], axis=-1)
    world = screen @ inverse.astype(np.float64).T
    direction = world[..., :3] / np.linalg.norm(world[..., :3], axis=-1, keepdims=True)
    return np.arccos(np.clip(-direction[..., 2], -1, 1))


def _red_drawn_for(renderer, projection):
    source = FrameTexture()
    source.upload(_side_by_side_picture_whose_red_is_the_distance_from_each_eyes_center())
    target = RenderTarget()
    target.ensure(DRAWN_PX, DRAWN_PX)
    try:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target.fbo)
        GL.glViewport(0, 0, DRAWN_PX, DRAWN_PX)
        renderer.begin_eye()
        renderer.draw_immersive(
            immersive_wrap(projection), source.texture, _inverse_of_looking_straight_ahead(), 0)
        pixels = np.zeros((DRAWN_PX, DRAWN_PX, 4), np.uint8)
        GL.glReadPixels(0, 0, DRAWN_PX, DRAWN_PX, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, pixels)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
    finally:
        target.close()
        source.close()
    return pixels[..., 0] / 255.0


@pytest.mark.parametrize("projection", list(RADIUS_READ_AT))
def test_each_fisheye_reads_the_picture_out_along_its_own_curve(renderer, projection):
    off_axis = _angle_off_axis_at_each_drawn_pixel(_inverse_of_looking_straight_ahead())
    away_from_the_center = off_axis > math.radians(5)

    red = _red_drawn_for(renderer, projection)

    difference = np.abs(red - RADIUS_READ_AT[projection](off_axis))[away_from_the_center]
    assert difference.max() < CLOSE_ENOUGH, (projection, float(difference.max()))


def test_the_three_curves_draw_three_different_pictures(renderer):
    drawn = [_red_drawn_for(renderer, projection) for projection in RADIUS_READ_AT]

    for first, second in ((0, 1), (0, 2), (1, 2)):
        assert np.abs(drawn[first] - drawn[second]).mean() > AT_LEAST_THIS_DIFFERENT, (first, second)
