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
    EQUIRECT_180_SBS,
    FISHEYE_180_SBS,
    FISHEYE_200_EQUISOLID_SBS,
    FISHEYE_200_STEREOGRAPHIC_SBS,
    MKX200_SBS,
    RECTILINEAR_SBS,
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
LOOKS_OUT_TO = math.radians(80)
CLOSE_ENOUGH = 0.02
AT_LEAST_THIS_DIFFERENT = 0.02
EDGE_BAND = 0.03

RADIUS_READ_AT = {
    MKX200_SBS: lambda off_axis: off_axis / math.radians(100),
    FISHEYE_200_STEREOGRAPHIC_SBS: lambda off_axis: np.tan(off_axis / 2) / math.tan(math.radians(50)),
    FISHEYE_200_EQUISOLID_SBS: lambda off_axis: np.sin(off_axis / 2) / math.sin(math.radians(50)),
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


def _side_by_side(eye):
    return np.concatenate([eye, eye], axis=1)


def _picture_whose_red_is_the_distance_from_each_eyes_center():
    ys, xs = np.mgrid[0:SOURCE_EYE_PX, 0:SOURCE_EYE_PX]
    distance = np.hypot((xs + 0.5) / SOURCE_EYE_PX - 0.5, (ys + 0.5) / SOURCE_EYE_PX - 0.5) / 0.5
    eye = np.zeros((SOURCE_EYE_PX, SOURCE_EYE_PX, 4), np.uint8)
    eye[..., 0] = (np.clip(distance, 0, 1) * 255).astype(np.uint8)
    eye[..., 3] = 255
    return _side_by_side(eye)


def _picture_whose_red_and_green_are_where_in_its_eye_each_pixel_is():
    across = (np.arange(SOURCE_EYE_PX) + 0.5) / SOURCE_EYE_PX
    eye = np.zeros((SOURCE_EYE_PX, SOURCE_EYE_PX, 4), np.uint8)
    eye[..., 0] = np.round(across[None, :] * 255).astype(np.uint8)
    eye[..., 1] = np.round(across[::-1][:, None] * 255).astype(np.uint8)
    eye[..., 3] = 255
    return _side_by_side(eye)


def _inverse_of_looking_straight_ahead():
    projection = fov_to_projection_matrix(
        -LOOKS_OUT_TO, LOOKS_OUT_TO, LOOKS_OUT_TO, -LOOKS_OUT_TO, 0.05, 100.0)
    return np.linalg.inv(projection).astype(np.float32)


def _direction_at_each_drawn_pixel():
    ys, xs = np.mgrid[0:DRAWN_PX, 0:DRAWN_PX]
    screen = np.stack([
        (xs + 0.5) / DRAWN_PX * 2 - 1, (ys + 0.5) / DRAWN_PX * 2 - 1,
        -np.ones(xs.shape), np.ones(xs.shape),
    ], axis=-1)
    world = screen @ _inverse_of_looking_straight_ahead().astype(np.float64).T
    return world[..., :3] / np.linalg.norm(world[..., :3], axis=-1, keepdims=True)


def _angle_off_axis_at_each_drawn_pixel():
    return np.arccos(np.clip(-_direction_at_each_drawn_pixel()[..., 2], -1, 1))


def _where_in_the_eye_an_equirect_reads(fov_deg):
    direction = _direction_at_each_drawn_pixel()
    theta = np.arctan2(direction[..., 0], -direction[..., 2])
    phi = np.arcsin(np.clip(direction[..., 1], -1, 1))
    return np.stack([theta, phi], axis=-1) / math.radians(fov_deg) + 0.5


def _where_in_the_eye_a_rectilinear_reads(fov_deg):
    direction = _direction_at_each_drawn_pixel()
    ahead = direction[..., 2] < 0
    depth = np.where(ahead, -direction[..., 2], 1.0)
    reads = 0.5 * direction[..., :2] / (depth[..., None] * math.tan(math.radians(fov_deg) / 2)) + 0.5
    return np.where(ahead[..., None], reads, -1.0)


def _where_in_the_eye_a_fisheye_reads(fov_deg):
    direction = _direction_at_each_drawn_pixel()
    off_axis = _angle_off_axis_at_each_drawn_pixel()
    planar = direction[..., :2] / np.maximum(np.linalg.norm(direction[..., :2], axis=-1, keepdims=True), 1e-9)
    return 0.5 + 0.5 * (off_axis / math.radians(fov_deg / 2))[..., None] * planar


def _made_taller(reads, height):
    return np.stack([reads[..., 0], 0.5 + (reads[..., 1] - 0.5) / height], axis=-1)


def _drawn(renderer, wrap, picture):
    source = FrameTexture()
    source.upload(picture)
    target = RenderTarget()
    target.ensure(DRAWN_PX, DRAWN_PX)
    try:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target.fbo)
        GL.glViewport(0, 0, DRAWN_PX, DRAWN_PX)
        renderer.begin_eye()
        renderer.draw_immersive(wrap, source.texture, _inverse_of_looking_straight_ahead(), 0)
        pixels = np.zeros((DRAWN_PX, DRAWN_PX, 4), np.uint8)
        GL.glReadPixels(0, 0, DRAWN_PX, DRAWN_PX, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, pixels)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
    finally:
        target.close()
        source.close()
    return pixels[..., :3] / 255.0


def _red_drawn_for(renderer, projection):
    return _drawn(
        renderer, immersive_wrap(projection),
        _picture_whose_red_is_the_distance_from_each_eyes_center())[..., 0]


def _the_coordinates_it_reads_are_where_it_should(renderer, wrap, reads):
    drawn = _drawn(renderer, wrap, _picture_whose_red_and_green_are_where_in_its_eye_each_pixel_is())

    inside = np.all((reads > EDGE_BAND) & (reads < 1 - EDGE_BAND), axis=-1)
    outside = np.any((reads < -EDGE_BAND) | (reads > 1 + EDGE_BAND), axis=-1)
    assert inside.sum() > DRAWN_PX, "the check reached too little of the picture to say anything"
    assert np.abs(drawn[..., :2] - reads)[inside].max() < CLOSE_ENOUGH
    assert np.all(drawn[outside] == 0.0)


@pytest.mark.parametrize("projection", list(RADIUS_READ_AT))
def test_each_fisheye_reads_the_picture_out_along_its_own_curve(renderer, projection):
    off_axis = _angle_off_axis_at_each_drawn_pixel()
    away_from_the_center = off_axis > math.radians(5)

    red = _red_drawn_for(renderer, projection)

    difference = np.abs(red - RADIUS_READ_AT[projection](off_axis))[away_from_the_center]
    assert difference.max() < CLOSE_ENOUGH, (projection, float(difference.max()))


def test_the_three_curves_draw_three_different_pictures(renderer):
    drawn = [_red_drawn_for(renderer, projection) for projection in RADIUS_READ_AT]

    for first, second in ((0, 1), (0, 2), (1, 2)):
        assert np.abs(drawn[first] - drawn[second]).mean() > AT_LEAST_THIS_DIFFERENT, (first, second)


@pytest.mark.parametrize("fov_deg", [180.0, 130.0])
def test_an_equirect_reads_the_picture_across_the_angle_it_is_given(renderer, fov_deg):
    _the_coordinates_it_reads_are_where_it_should(
        renderer, immersive_wrap(EQUIRECT_180_SBS, fov_deg=fov_deg),
        _where_in_the_eye_an_equirect_reads(fov_deg))


@pytest.mark.parametrize("fov_deg", [120.0, 90.0])
def test_a_rectilinear_window_reads_the_picture_across_the_angle_it_is_given(renderer, fov_deg):
    _the_coordinates_it_reads_are_where_it_should(
        renderer, immersive_wrap(RECTILINEAR_SBS, fov_deg=fov_deg),
        _where_in_the_eye_a_rectilinear_reads(fov_deg))


@pytest.mark.parametrize("fov_deg", [190.0, 140.0])
def test_a_fisheye_reads_the_picture_out_to_the_angle_it_is_given_and_past_its_circle(renderer, fov_deg):
    _the_coordinates_it_reads_are_where_it_should(
        renderer, immersive_wrap(FISHEYE_180_SBS, fov_deg=fov_deg),
        _where_in_the_eye_a_fisheye_reads(fov_deg))


@pytest.mark.parametrize("projection, reads", [
    (EQUIRECT_180_SBS, _where_in_the_eye_an_equirect_reads(180.0)),
    (RECTILINEAR_SBS, _where_in_the_eye_a_rectilinear_reads(120.0)),
    (FISHEYE_180_SBS, _where_in_the_eye_a_fisheye_reads(180.0)),
], ids=["equirect", "rectilinear", "fisheye"])
def test_a_height_stretches_the_picture_about_its_middle(renderer, projection, reads):
    _the_coordinates_it_reads_are_where_it_should(
        renderer, immersive_wrap(projection, height=1.5), _made_taller(reads, 1.5))
