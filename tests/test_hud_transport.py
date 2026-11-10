from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from funestra.modes import SatellitesMode
from funestra.satellite_hud import HudCell

from fun_time.hud_transport import HudPublisher, hud_model
from fun_time.lock_hud import ACTION_LIMIT, SatelliteHud
from fun_time.thumbnail_cache import thumbnail_path
from tests.drive_fakes import folders_listed


def _hud(**overrides) -> SatelliteHud:
    base = {
        "funestra": "portrait", "locked": True, "title": "Locked",
        "current": "C:/v/cur.mp4", "seed_siblings": ["C:/v/s1.mp4"], "action_siblings": ["C:/v/a1.mp4"],
        "current_action": "alpha", "action_lines": ("gamma",),
    }
    base.update(overrides)
    return SatelliteHud(**base)


def _thumb(path: str) -> Path:
    return Path(path).with_suffix(".jpg")


def test_the_map_never_waits_on_a_frame_grab():
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        model = hud_model(_hud(), Path("C:/state/thumbs"))

    assert model.funestra == "portrait"
    assert model.locked is True
    assert model.title == "Locked"
    assert model.current_action == "alpha"
    assert model.corner == HudCell(path="C:/v/cur.mp4", thumb=str(_thumb("C:/v/cur.mp4")))
    assert model.seeds == (HudCell(path="C:/v/s1.mp4", thumb=str(_thumb("C:/v/s1.mp4"))),)
    assert model.actions == (
        HudCell(path="C:/v/a1.mp4", thumb=str(_thumb("C:/v/a1.mp4")), label="gamma"),
    )


def _a_map_whose_thumbnails_are_made(tmp_path: Path) -> tuple[SatelliteHud, Path, Path]:
    library = tmp_path / "vids"
    library.mkdir()
    cache = tmp_path / "thumbs"
    cache.mkdir()
    videos = []
    for name in ("cur", "s1", "a1"):
        video = library / f"{name}.mp4"
        video.write_bytes(b"x")
        thumbnail_path(video, cache).write_bytes(b"jpeg")
        videos.append(str(video))
    hud = _hud(current=videos[0], seed_siblings=[videos[1]], action_siblings=[videos[2]])
    return hud, library, cache


def test_a_map_built_on_the_command_loop_lists_its_folder_once(tmp_path: Path):
    hud, _library, cache = _a_map_whose_thumbnails_are_made(tmp_path)

    with folders_listed() as read:
        model = hud_model(hud, cache)

    assert len(read) == 1
    assert model.corner == HudCell(
        path=hud.current, thumb=str(thumbnail_path(hud.current, cache)))


def test_the_funestra_is_handed_pictures_outside_the_library(tmp_path: Path):
    hud, library, cache = _a_map_whose_thumbnails_are_made(tmp_path)

    model = hud_model(hud, cache)

    pictures = [Path(cell.thumb) for cell in (model.corner, *model.seeds, *model.actions)]
    assert len(pictures) == 3
    assert all(cache in picture.parents for picture in pictures)
    assert not any(library in picture.parents for picture in pictures)


def test_hud_payload_carries_whether_this_satellite_is_the_active_one():
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        active = hud_model(_hud(active=True), Path("C:/state/thumbs"))
        idle = hud_model(_hud(active=False), Path("C:/state/thumbs"))

    assert active.active is True
    assert idle.active is False

def test_hud_payload_carries_whether_the_video_is_a_favorite():
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        assert hud_model(_hud(is_favorite=True), Path("C:/t")).is_favorite is True
        assert hud_model(_hud(), Path("C:/t")).is_favorite is False


def _band(model) -> dict[str, object]:
    return {button.command.removeprefix(f"{model.funestra}_"): button for button in model.rows[-1]}


def test_hud_payload_declares_the_browse_order_pair_only_where_the_satellite_names_an_order():
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        newest = _band(hud_model(_hud(latest=True), Path("C:/t")))
        shuffled = _band(hud_model(_hud(latest=False), Path("C:/t")))
        unswitchable = _band(hud_model(_hud(), Path("C:/t")))

    assert newest["latest"].lit
    assert not newest["shuffle"].lit
    assert shuffled["shuffle"].lit
    assert not shuffled["latest"].lit
    assert "shuffle" not in unswitchable
    assert "latest" not in unswitchable


def test_hud_payload_declares_the_satellites_buttons_lit_off_its_own_state():
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        band = _band(hud_model(_hud(locked=True, favorites_filter=True), Path("C:/t")))

    assert band["lock"].lit
    assert band["fmode"].lit
    assert not band["trash"].lit
    assert all(button.command.startswith("portrait_") for button in band.values())


def test_hud_payload_declares_the_mode_row_where_the_session_hosts_an_origenerator():
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        hosted = hud_model(_hud(mode=SatellitesMode.ORIGENERATOR), Path("C:/t"))
        plain = hud_model(_hud(), Path("C:/t"))

    assert [button.command for button in hosted.rows[0]] == [
        "portrait_kino_activate", "portrait_origenerator_activate", "portrait_minimize",
        "portrait_crown"]
    assert hosted.rows[0][1].lit
    assert len(plain.rows) == 1


def test_hud_payload_keeps_the_corner_without_a_thumbnail_but_drops_siblings():
    with patch("fun_time.hud_transport.cached_thumbnail", return_value=None):
        payload = hud_model(_hud(), Path("C:/state/thumbs"))

    assert payload.corner == HudCell(path="C:/v/cur.mp4", thumb="")
    assert payload.seeds == ()
    assert payload.actions == ()


def test_a_hud_drawn_before_its_stills_are_cached_decodes_no_frame(tmp_path):
    playing, seed, action = (str(tmp_path / name) for name in ("one.mp4", "two.mp4", "three.mp4"))
    for video in (playing, seed, action):
        Path(video).write_bytes(b"x")
    hud = _hud(current=playing, seed_siblings=[seed], action_siblings=[action])

    with patch("cv2.VideoCapture") as decoder:
        payload = hud_model(hud, tmp_path / "cache")

    assert not decoder.called
    assert payload.corner == HudCell(path=playing, thumb="")


def test_hud_payload_has_no_corner_when_nothing_is_playing():
    with patch("fun_time.hud_transport.cached_thumbnail", return_value=None):
        payload = hud_model(
            _hud(current="", seed_siblings=[], action_siblings=[]), Path("C:/t"))

    assert payload.corner is None


def test_hud_payload_marks_the_cell_actually_on_screen():
    hud = _hud(active_loop="seed", playing="C:/v/s1.mp4")
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        payload = hud_model(hud, Path("C:/t"))

    assert payload.playing == ("seed", 0)
    assert payload.active_loop == "seed"


def test_a_running_loop_hands_the_funestra_every_item_it_cycles():
    seeds = [f"C:/v/s{i}.mp4" for i in range(12)]
    hud = _hud(active_loop="seed", seed_siblings=seeds, playing=seeds[9])

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        payload = hud_model(hud, Path("C:/t"))

    assert [cell.path for cell in payload.seeds] == seeds
    assert payload.playing == ("seed", 9)


def test_a_loop_item_with_no_thumbnail_yet_keeps_its_cell_and_those_after_it():
    seeds = ["C:/v/s0.mp4", "C:/v/s1.mp4", "C:/v/s2.mp4"]
    hud = _hud(active_loop="seed", seed_siblings=seeds, playing=seeds[2])

    with patch("fun_time.hud_transport.cached_thumbnail",
               side_effect=lambda p, _d, _listed=None: None if p == "C:/v/s1.mp4" else _thumb(p)):
        payload = hud_model(hud, Path("C:/t"))

    assert [cell.path for cell in payload.seeds] == seeds
    assert payload.seeds[1].thumb == ""
    assert payload.playing == ("seed", 2)


def test_the_axis_a_loop_is_not_running_on_stays_capped():
    actions = [f"C:/v/a{i}.mp4" for i in range(9)]
    hud = _hud(active_loop="seed", action_siblings=actions, action_lines=tuple("x" * 9))

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        payload = hud_model(hud, Path("C:/t"))

    assert len(payload.actions) == ACTION_LIMIT


def test_hud_payload_falls_back_to_the_corner_for_an_off_map_video():
    hud = _hud(playing="C:/v/elsewhere.mp4")
    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        payload = hud_model(hud, Path("C:/t"))

    assert payload.playing == ("corner", 0)


def test_publish_writes_the_file_only_when_the_hud_changes(tmp_path: Path):
    hud_file = tmp_path / "portrait_hud.json"
    publisher = HudPublisher({"portrait": hud_file}, tmp_path / "thumbs")

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        assert publisher.publish("portrait", _hud()) is True
        assert publisher.publish("portrait", _hud()) is False
        assert publisher.publish("portrait", _hud(locked=False, title="Unlocked")) is True

    assert json.loads(hud_file.read_text(encoding="utf-8"))["locked"] is False


def test_a_locked_huds_dropped_write_is_retried_next_tick(tmp_path: Path):
    hud_file = tmp_path / "portrait_hud.json"
    publisher = HudPublisher({"portrait": hud_file}, tmp_path / "thumbs")

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)):
        with patch("fun_time.hud_transport.publish_whole", return_value=False):
            assert publisher.publish("portrait", _hud()) is False
        assert publisher.publish("portrait", _hud()) is True

    assert json.loads(hud_file.read_text(encoding="utf-8"))["locked"] is True


def test_a_hud_polled_every_frame_is_swapped_in_whole_never_written_in_place(tmp_path: Path):
    hud_file = tmp_path / "portrait_hud.json"
    publisher = HudPublisher({"portrait": hud_file}, tmp_path / "thumbs")
    opened_to_write: list[Path] = []
    real_open = Path.open

    def noting(path, mode="r", *args, **kwargs):
        if "w" in mode:
            opened_to_write.append(path)
        return real_open(path, mode, *args, **kwargs)

    with patch("fun_time.hud_transport.cached_thumbnail", side_effect=lambda p, _d, _listed=None: _thumb(p)), \
            patch.object(Path, "open", noting):
        assert publisher.publish("portrait", _hud()) is True

    assert opened_to_write
    assert hud_file not in opened_to_write
    assert json.loads(hud_file.read_text(encoding="utf-8"))["locked"] is True


def test_publish_ignores_a_satellite_with_no_file(tmp_path: Path):
    publisher = HudPublisher({}, tmp_path / "thumbs")

    assert publisher.publish("portrait", _hud()) is False


