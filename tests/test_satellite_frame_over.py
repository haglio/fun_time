from __future__ import annotations

from PIL import Image

from satellite.frame_over import FRAME_OVERLAY_ID, FrameOver, fitted_bgra
from satellite.hud_overlay import HUD_OVERLAY_ID
from tests.satellite_fakes import FakeSatellitePlayer


def _frame(tmp_path, name="frame.png", size=(20, 10), color=(200, 30, 30)):
    path = tmp_path / name
    Image.new("RGB", size, color).save(path)
    return path


def test_a_frame_is_fitted_inside_the_screen_on_black(tmp_path):
    bgra = fitted_bgra(_frame(tmp_path), 40, 40)

    assert bgra.shape == (40, 40, 4)
    assert (bgra[:, :, 3] == 255).all()
    assert tuple(bgra[20, 20, :3]) == (30, 30, 200)
    assert tuple(bgra[0, 20, :3]) == (0, 0, 0)
    assert tuple(bgra[39, 20, :3]) == (0, 0, 0)


class _CountingPlayer(FakeSatellitePlayer):
    def __init__(self):
        super().__init__()
        self.painted = 0

    def overlay(self, ident, x, y, bgra):
        self.painted += 1
        super().overlay(ident, x, y, bgra)


def test_a_frame_is_painted_once_and_taken_off_when_it_goes(tmp_path):
    player = _CountingPlayer()
    painter = FrameOver(player)
    frame = _frame(tmp_path)

    painter.paint(frame, 40, 40)
    painter.paint(frame, 40, 40)
    assert player.painted == 1
    assert player.overlays[FRAME_OVERLAY_ID][:2] == (0, 0)

    painter.paint(None, 40, 40)
    assert FRAME_OVERLAY_ID not in player.overlays


def test_a_frame_that_cannot_be_read_leaves_the_last_one_up(tmp_path):
    player = _CountingPlayer()
    painter = FrameOver(player)
    painter.paint(_frame(tmp_path), 40, 40)

    painter.paint(tmp_path / "not written yet.png", 40, 40)

    assert player.painted == 1
    assert FRAME_OVERLAY_ID in player.overlays


def test_a_frame_is_painted_under_the_panel_since_mpv_draws_the_lower_id_first():
    assert FRAME_OVERLAY_ID < HUD_OVERLAY_ID
