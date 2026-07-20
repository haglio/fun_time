"""Publishing each satellite's HUD panel to the file its player renders from."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fun_time.hud_transport import HudPublisher, hud_payload
from fun_time.lock_hud import ACTION_LIMIT, HudPanel


def _panel(**overrides) -> HudPanel:
    base = dict(
        side="portrait", locked=True, lock_label="Locked",
        current="C:/v/cur.mp4", seed_siblings=["C:/v/s1.mp4"], action_siblings=["C:/v/a1.mp4"],
        current_action="alpha", action_labels=("gamma",),
    )
    base.update(overrides)
    return HudPanel(**base)


def _thumb(path: str) -> Path:
    """A stand-in cached thumbnail: <name>.jpg beside the clip."""
    return Path(path).with_suffix(".jpg")


def test_hud_payload_carries_the_map_with_its_cached_thumbnails():
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d: _thumb(p)):
        payload = hud_payload(_panel(), Path("C:/state/thumbs"))

    assert payload["side"] == "portrait"
    assert payload["locked"] is True
    assert payload["lock_label"] == "Locked"
    assert payload["current_action"] == "alpha"
    assert payload["corner"] == {"path": "C:/v/cur.mp4", "thumb": str(_thumb("C:/v/cur.mp4"))}
    assert payload["seeds"] == [{"path": "C:/v/s1.mp4", "thumb": str(_thumb("C:/v/s1.mp4"))}]
    assert payload["actions"] == [
        {"path": "C:/v/a1.mp4", "thumb": str(_thumb("C:/v/a1.mp4")), "label": "gamma"}
    ]


def test_hud_payload_carries_whether_this_side_is_the_active_one():
    """The player draws the dot; only fun_time knows which side has the floor."""
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d: _thumb(p)):
        active = hud_payload(_panel(active=True), Path("C:/state/thumbs"))
        idle = hud_payload(_panel(active=False), Path("C:/state/thumbs"))

    assert active["active"] is True
    assert idle["active"] is False


def test_hud_payload_keeps_the_corner_without_a_thumbnail_but_drops_siblings():
    """The corner is the clip on screen, so it stays (the player draws a
    placeholder); a sibling whose frame the prewarm hasn't produced simply isn't
    on the map yet, exactly as the Qt HUD skipped it."""
    with patch("fun_time.hud_transport.cached_thumbnail", return_value=None):
        payload = hud_payload(_panel(), Path("C:/state/thumbs"))

    assert payload["corner"] == {"path": "C:/v/cur.mp4", "thumb": ""}
    assert payload["seeds"] == []
    assert payload["actions"] == []


def test_hud_payload_has_no_corner_when_nothing_is_playing():
    with patch("fun_time.hud_transport.cached_thumbnail", return_value=None):
        payload = hud_payload(
            _panel(current="", seed_siblings=[], action_siblings=[]), Path("C:/t"))

    assert payload["corner"] is None


def test_hud_payload_marks_the_cell_actually_on_screen():
    """Mid-loop the map holds still and a non-corner cell is what is playing; the
    payload names that cell so the player lights it instead of the corner."""
    panel = _panel(active_loop="seed", playing="C:/v/s1.mp4")
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d: _thumb(p)):
        payload = hud_payload(panel, Path("C:/t"))

    assert payload["playing"] == ["seed", 0]
    assert payload["active_loop"] == "seed"


def test_a_running_loop_publishes_every_member_it_cycles():
    """The player windows the looped axis around the clip on screen, so it has to be
    given the whole loop.  Capping it at the handful of cells a map can draw is what
    left the highlight nowhere to be once the loop advanced past them — the user saw
    three thumbnails, then a stretch with nothing lit and an unrecognisable clip."""
    seeds = [f"C:/v/s{i}.mp4" for i in range(12)]
    panel = _panel(active_loop="seed", seed_siblings=seeds, playing=seeds[9])

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d: _thumb(p)):
        payload = hud_payload(panel, Path("C:/t"))

    assert [cell["path"] for cell in payload["seeds"]] == seeds
    assert payload["playing"] == ["seed", 9]


def test_a_running_loop_keeps_a_member_whose_thumbnail_is_not_cached_yet():
    """Dropping it would renumber every cell behind it and slide the player's window
    off the clip on screen, so a loop member with no frame yet is published with an
    empty thumbnail — drawn as a placeholder, in the loop's own order."""
    seeds = ["C:/v/s0.mp4", "C:/v/s1.mp4", "C:/v/s2.mp4"]
    panel = _panel(active_loop="seed", seed_siblings=seeds, playing=seeds[2])

    with patch("fun_time.hud_transport.cached_thumbnail",
               side_effect=lambda p, _d: None if p == "C:/v/s1.mp4" else _thumb(p)):
        payload = hud_payload(panel, Path("C:/t"))

    assert [cell["path"] for cell in payload["seeds"]] == seeds
    assert payload["seeds"][1]["thumb"] == ""
    assert payload["playing"] == ["seed", 2]


def test_the_axis_a_loop_is_not_running_on_stays_capped(tmp_path: Path):
    """Only the looped axis needs the whole group; the other one is still the browse
    map, so it keeps its draw cap and its cached-only rule."""
    actions = [f"C:/v/a{i}.mp4" for i in range(9)]
    panel = _panel(active_loop="seed", action_siblings=actions, action_labels=tuple("x" * 9))

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d: _thumb(p)):
        payload = hud_payload(panel, Path("C:/t"))

    assert len(payload["actions"]) == ACTION_LIMIT


def test_hud_payload_falls_back_to_the_corner_for_an_off_map_clip():
    """A satellite that auto-advanced off the drawn map has no cell to light, so
    the corner stays bright rather than nothing at all."""
    panel = _panel(playing="C:/v/elsewhere.mp4")
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d: _thumb(p)):
        payload = hud_payload(panel, Path("C:/t"))

    assert payload["playing"] == ["corner", 0]


def test_publish_writes_the_file_only_when_the_panel_changes(tmp_path: Path):
    """The player re-renders whenever the file changes, so an unchanged tick must
    not rewrite it — the dispatch loop publishes many times per clip."""
    hud_file = tmp_path / "portrait_hud.json"
    publisher = HudPublisher({"portrait": hud_file}, tmp_path / "thumbs")

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d: _thumb(p)):
        assert publisher.publish("portrait", _panel()) is True
        assert publisher.publish("portrait", _panel()) is False
        assert publisher.publish("portrait", _panel(locked=False, lock_label="Unlocked")) is True

    assert json.loads(hud_file.read_text(encoding="utf-8"))["locked"] is False


def test_publish_republishes_a_panel_whose_write_never_landed(tmp_path: Path):
    """A dropped write must not be remembered as published: the panel the player
    is reading would then stay stale until something else changed it, which for a
    locked satellite can be a very long time.

    (Getting the panel onto disk whole, and riding out a reader's hold on the
    file, is ``player_core.file_channel.publish_whole`` and is covered there.)"""
    hud_file = tmp_path / "portrait_hud.json"
    publisher = HudPublisher({"portrait": hud_file}, tmp_path / "thumbs")

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d: _thumb(p)):
        with patch("fun_time.hud_transport.publish_whole", return_value=False):
            assert publisher.publish("portrait", _panel()) is False
        # The very same panel, now that the file is free again.
        assert publisher.publish("portrait", _panel()) is True

    assert json.loads(hud_file.read_text(encoding="utf-8"))["locked"] is True


def test_publish_ignores_a_side_with_no_file(tmp_path: Path):
    publisher = HudPublisher({}, tmp_path / "thumbs")

    assert publisher.publish("portrait", _panel()) is False
