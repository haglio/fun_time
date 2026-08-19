"""The satellite's HUD overlay: composite the published panel, take its clicks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from player_core.satellite_hud import MARGIN
from satellite.hud_overlay import HudOverlay
from tests.satellite_fakes import FakeSatellitePlayer


@pytest.fixture
def panel(tmp_path: Path) -> Path:
    """A published HUD panel on disk, with one seed and one action row."""
    from PIL import Image

    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (40, 60), (90, 90, 90)).save(thumb)
    path = tmp_path / "portrait_hud.json"
    path.write_text(json.dumps({
        "side": "portrait", "locked": False, "lock_label": "Unlocked",
        "current_action": "alpha",
        "corner": {"path": "C:/v/cur.mp4", "thumb": str(thumb)},
        "seeds": [{"path": "C:/v/s1.mp4", "thumb": str(thumb)}],
        "actions": [{"path": "C:/v/a1.mp4", "thumb": str(thumb), "label": "gamma"}],
    }), encoding="utf-8")
    return path


def _overlay(tmp_path: Path, panel_path: Path, player, clock=None) -> HudOverlay:
    return HudOverlay(
        hud_file=panel_path, command_file=tmp_path / "dashboard_cmd.txt",
        player=player, clock=clock or (lambda: 0.0),
    )


def _commands(tmp_path: Path) -> list[str]:
    path = tmp_path / "dashboard_cmd.txt"
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_tick_composites_the_panel_at_the_hud_inset(tmp_path: Path, panel: Path):
    player = FakeSatellitePlayer()
    _overlay(tmp_path, panel, player).tick()

    assert len(player.overlays) == 1
    (x, y, bgra), = player.overlays.values()
    assert (x, y) == (MARGIN, MARGIN)
    assert bgra.shape[2] == 4


def test_tick_redraws_only_when_the_published_panel_changes(tmp_path: Path, panel: Path):
    """fun_time rewrites the file only on a real change, but the player polls it
    every frame — an unchanged read must not re-render the whole panel."""
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)

    overlay.tick()
    first = player.overlays[overlay.overlay_id][2]
    overlay.tick()
    assert player.overlays[overlay.overlay_id][2] is first

    panel.write_text(panel.read_text(encoding="utf-8").replace(
        '"locked": false', '"locked": true'), encoding="utf-8")
    overlay.tick()
    assert player.overlays[overlay.overlay_id][2] is not first


def test_tick_redraws_when_the_clip_on_screen_changes(tmp_path: Path, panel: Path):
    """The HUD names the file the player has open, and a satellite left alone walks
    its playlist by itself — fun_time republishes the panel only when the map behind
    it moves, so the name has to redraw off the player's own answer or it would sit
    on a clip that had already rolled past."""
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)

    overlay.tick(video="one")
    first = player.overlays[overlay.overlay_id][2]
    overlay.tick(video="one")
    assert player.overlays[overlay.overlay_id][2] is first

    overlay.tick(video="two")
    assert player.overlays[overlay.overlay_id][2] is not first


def test_no_panel_file_means_no_overlay(tmp_path: Path):
    """A satellite fun_time hasn't published a HUD for (an integration run, or
    before the first publish) simply shows no map."""
    player = FakeSatellitePlayer()
    _overlay(tmp_path, tmp_path / "absent.json", player).tick()

    assert player.overlays == {}


def test_the_overlay_is_removed_when_the_panel_goes_away(tmp_path: Path, panel: Path):
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()

    panel.unlink()
    overlay.tick()

    assert player.overlays == {}


def test_a_panel_that_cannot_be_read_this_frame_keeps_the_map_up(
    tmp_path: Path, panel: Path, monkeypatch: pytest.MonkeyPatch
):
    """fun_time replaces this file while the player polls it 60x/s, so a read can
    lose that race and come back as a sharing violation.

    That is not the panel going away — it is one frame that could not see it.
    Tearing the overlay down for it, and rebuilding it on the next frame, is a
    HUD that blinks.
    """
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()
    drawn = player.overlays[overlay.overlay_id][2]

    real_read_text = Path.read_text

    def refuse(self, *args, **kwargs):
        if self == panel:
            raise PermissionError(32, "The process cannot access the file")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)
    overlay.tick()

    assert player.overlays[overlay.overlay_id][2] is drawn


def test_a_single_click_posts_the_switch_once_its_window_lapses(tmp_path: Path, panel: Path):
    """A click on a thumbnail could be the first half of a double-click, so the
    switch is posted by a later tick — not by the press itself."""
    now = [0.0]
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player, clock=lambda: now[0])
    overlay.tick()
    corner_rect = overlay.targets.click[0][0]

    overlay.press(corner_rect[0] + MARGIN + 2, corner_rect[1] + MARGIN + 2)
    overlay.tick()
    assert _commands(tmp_path) == []

    now[0] = 1.0
    overlay.tick()
    assert _commands(tmp_path) == ["portrait_play_video|C:/v/cur.mp4"]


def test_a_double_click_locks_instead_of_switching(tmp_path: Path, panel: Path):
    now = [0.0]
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player, clock=lambda: now[0])
    overlay.tick()
    x, y, _w, _h = overlay.targets.click[1][0]  # the seed thumbnail

    overlay.press(x + MARGIN + 2, y + MARGIN + 2)
    now[0] = 0.2
    overlay.press(x + MARGIN + 2, y + MARGIN + 2)
    now[0] = 2.0
    overlay.tick()

    assert _commands(tmp_path) == ["portrait_lock_video|C:/v/s1.mp4"]


def test_a_loop_button_click_posts_at_once(tmp_path: Path, panel: Path):
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()
    rect = dict((kind, r) for r, kind in overlay.targets.loop)["seed"]

    overlay.press(rect[0] + MARGIN + 2, rect[1] + MARGIN + 2)

    assert _commands(tmp_path) == ["portrait_seed_loop"]


def test_a_press_outside_the_panel_posts_nothing(tmp_path: Path, panel: Path):
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()

    overlay.press(2, 2)          # inside the window, outside the HUD's inset
    overlay.press(2000, 2000)    # far off the panel

    assert _commands(tmp_path) == []


def test_hovering_a_button_redraws_with_its_tooltip(tmp_path: Path, panel: Path):
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()
    plain = player.overlays[overlay.overlay_id][2]
    rect = dict((kind, r) for r, kind in overlay.targets.loop)["action"]

    overlay.motion(rect[0] + MARGIN + 2, rect[1] + MARGIN + 2)

    assert player.overlays[overlay.overlay_id][2] is not plain
    # Moving off the button clears it again.
    overlay.motion(MARGIN + 2, MARGIN + 2)
    assert player.overlays[overlay.overlay_id][2] is not plain


def test_pressing_the_lit_filter_button_lifts_the_filter(tmp_path: Path, panel: Path):
    """The published filter is what makes the button a toggle, so a filter set any
    other way — spoken, or from the other side of the map — is lifted by pressing the
    button it lit."""
    player = FakeSatellitePlayer()
    panel.write_text(panel.read_text(encoding="utf-8").replace(
        '"side": "portrait"', '"side": "portrait", "filter_query": "alpha"'), encoding="utf-8")
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()

    rect = dict((name, r) for r, name in overlay.targets.filter)["alpha"]
    overlay.press(rect[0] + MARGIN + 2, rect[1] + MARGIN + 2)

    assert _commands(tmp_path) == ["portrait_no_filter"]


def test_pressing_an_unlit_filter_button_filters_to_its_act(tmp_path: Path, panel: Path):
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()

    rect = dict((name, r) for r, name in overlay.targets.filter)["gamma"]
    overlay.press(rect[0] + MARGIN + 2, rect[1] + MARGIN + 2)

    assert _commands(tmp_path) == ["filter_portrait_gamma"]


def test_the_published_loop_state_wins_over_the_optimistic_one(tmp_path: Path, panel: Path):
    """A click lights the button before fun_time answers, but the published panel
    is authoritative — a loop that ended must not stay lit."""
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()
    rect = dict((kind, r) for r, kind in overlay.targets.loop)["seed"]
    overlay.press(rect[0] + MARGIN + 2, rect[1] + MARGIN + 2)
    assert overlay.active_loop == "seed"

    panel.write_text(panel.read_text(encoding="utf-8").replace(
        '"side": "portrait"', '"side": "portrait", "active_loop": ""'), encoding="utf-8")
    overlay.tick()

    assert overlay.active_loop == ""


def test_display_suppressed_follows_the_published_satellites_mode(
    tmp_path: Path, panel: Path
):
    """In origenerator mode the run loop blacks the video out under the HUD —
    the mode arrives on the published panel, so the overlay is the one place
    the player learns it from."""
    player = FakeSatellitePlayer()
    overlay = _overlay(tmp_path, panel, player)
    overlay.tick()
    assert overlay.display_suppressed is False  # no satellites_mode published

    panel.write_text(panel.read_text(encoding="utf-8").replace(
        '"side": "portrait"',
        '"side": "portrait", "satellites_mode": "origenerator"'), encoding="utf-8")
    overlay.tick()
    assert overlay.display_suppressed is True

    panel.write_text(panel.read_text(encoding="utf-8").replace(
        '"satellites_mode": "origenerator"',
        '"satellites_mode": "player"'), encoding="utf-8")
    overlay.tick()
    assert overlay.display_suppressed is False
