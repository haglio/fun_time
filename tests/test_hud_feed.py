from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from app_support.osr2_device import Osr2Mode
from app_support.testing import file_channel_clock_at
from funestra.hud_button import Button
from funestra.hud_placement import HudCorner
from funestra.main_hud import OSR2_PARKED, MainHudModel, read_main_hud
from funestra.modes import MainMode, Osr2State, SatellitesMode
from funestra.osr2_visualizer import DriveHud, publish_drive
from funestra.satellite_hud import HudCell, HudModel, PaintedFoot, hud_text, parse_hud

from fun_time.bridge_records import BridgeConfig
from fun_time.config import SatelliteChannel
from fun_time.crown import Crown
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.hud_feed import PUBLISH_INTERVAL_S, HudFeed
from fun_time.hud_transport import HudPublisher
from fun_time.media_metadata import load_metadata, mark_favorite, metadata_path_for
from fun_time.osr2_section import DASHBOARD_OSR2_PART, osr2_button, osr2_part_path
from fun_time.shared_state import BridgeState, SatelliteState


def make_config(tmp_path, **overrides) -> BridgeConfig:
    settings = {
        "portrait": SatelliteChannel.in_state_dir(tmp_path, FunTimeFunestra.PORTRAIT),
        "landscape": SatelliteChannel.in_state_dir(tmp_path, FunTimeFunestra.LANDSCAPE),
        "trash_dir": tmp_path / "trash",
        "state_dir": tmp_path,
        "main_sources": "",
        "broker_mode_file": tmp_path / "rh_mode.txt",
        "genau_cmd_file": tmp_path / "rh_cmd.txt",
        "genau_paused_file": tmp_path / "rh_paused.txt",
        "genau_status_file": tmp_path / "genau_status.txt",
        "audio_paused_file": tmp_path / "audio_paused.txt",
        "audio_volume_file": tmp_path / "audio_volume.txt",
        "main_funestra_cmd_file": tmp_path / "main_player_cmd.txt",
        "main_funestra_paused_file": tmp_path / "main_player_paused.txt",
        "main_funestra_status_file": tmp_path / "main_player_status.txt",
        "dashboard_state_file": tmp_path / "dashboard_state.ini",
    }
    settings.update(overrides)
    return BridgeConfig(**settings)


def _hosted(state: BridgeState, *funestras: str) -> BridgeState:
    for funestra in funestras or ("portrait", "landscape"):
        state = state.with_satellite(FunTimeFunestra[funestra.upper()],
                                     mode=SatellitesMode.ORIGENERATOR, handed_over=True)
    return state


def hosting_config(tmp_path) -> BridgeConfig:
    return make_config(
        tmp_path, origenerator_enabled=True,
        origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")


def make_feed(tmp_path, *, config=None) -> HudFeed:
    publisher = HudPublisher(
        {"portrait": tmp_path / "portrait_hud.json",
         "landscape": tmp_path / "landscape_hud.json",
         "main_funestra": tmp_path / "main_player_console.json",
         DASHBOARD_OSR2_PART: osr2_part_path(tmp_path)},
        tmp_path / "thumbs",
        (),
    )
    return HudFeed(config=config or make_config(tmp_path), publisher=publisher)


def publish_satellite_status(path: Path, video, *,
                             speed: float = 1.0, duration_ms: int = 1000,
                             has_funscript: bool = False) -> None:
    path.write_text(
        f"video={video}\nposition_ms={round(0.1 * duration_ms)}\n"
        f"duration_ms={duration_ms}\npaused=0\nlocked=0\nspeed={speed}\n"
        f"has_funscript={int(has_funscript)}\n",
        encoding="utf-8",
    )


def hud(tmp_path, funestra: str) -> dict:
    return json.loads((tmp_path / f"{funestra}_hud.json").read_text(encoding="utf-8"))


def console(tmp_path) -> dict:
    return json.loads((tmp_path / "main_player_console.json").read_text(encoding="utf-8"))


def dashboard(tmp_path) -> dict:
    return json.loads(osr2_part_path(tmp_path).read_text(encoding="utf-8"))


def f_mode_button(published: dict) -> dict:
    return next(button for row in published["rows"] for button in row
                if button["command"].endswith("_fmode"))


def reset_button(published: dict) -> dict:
    return next(button for row in published["rows"] for button in row
                if button["command"].endswith("reset"))


def skip_ahead(published: dict) -> dict:
    return next(button for row in published["rows"] for button in row
                if button.get("command", "").endswith("_funscript_jump"))


def ten_second_steps(published: dict) -> list[dict]:
    return [button for row in published["rows"] for button in row
            if button.get("command", "").endswith(("_nudge_prev", "_nudge_next"))]


def version_button(published: dict) -> dict:
    return next(button for row in published["rows"] for button in row
                if button["command"].endswith("_cycle_version"))


def broker_is_up(published: dict) -> bool:
    (broker,) = published["osr2_controls"]
    assert broker["command"] == "broker_panel"
    return bool(broker.get("lit")) and not broker.get("warn")


class TestHudPublishing:
    def test_each_satellites_hud_carries_its_own_video_and_lock(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4")
        state = replace(state, portrait=SatelliteState(locked=True, filter="alpha"))

        feed.publish(state)

        portrait = hud(tmp_path, "portrait")
        landscape = hud(tmp_path, "landscape")
        assert portrait["funestra"] == "portrait"
        assert portrait["locked"] is True
        assert portrait["title"] == "Locked · Shuffle · alpha"
        assert portrait["corner"]["path"] == "C:/v/p.mp4"
        assert landscape["locked"] is False
        assert landscape["corner"]["path"] == "C:/v/l.mp4"


    def test_a_satellite_the_hosted_app_has_wears_that_apps_own_hud(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        hosted = HudModel(
            funestra="portrait", title="Unlocked · Shuffle",
            corner=HudCell(path="C:/g/scene one.png"),
            seeds=(HudCell(path="C:/g/scene two.png"),), seed_count=2,
            rows=((Button("portrait_next", "N", "Next slide"),),),
        )
        (tmp_path / "origenerator_portrait_hud.json").write_text(
            hud_text(hosted), encoding="utf-8")

        feed.publish(_hosted(BridgeState(active_funestra=2)))

        portrait = parse_hud((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        assert portrait.corner.path == "C:/g/scene one.png"
        assert portrait.seeds == hosted.seeds
        assert portrait.title == "Unlocked · Shuffle"
        assert portrait.active is True
        assert [button.command for button in portrait.rows[0]] == [
            "portrait_kino_activate", "portrait_origenerator_activate", "portrait_minimize",
            "portrait_crown"]
        assert [button.lit for button in portrait.rows[0][:2]] == [False, True]
        assert [button.command for button in portrait.rows[1]] == ["portrait_next"]

    def test_the_job_queue_the_hosted_app_painted_reaches_that_satellites_hud(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        foot = PaintedFoot(file=str(tmp_path / "origenerator_portrait_hud_foot_a1.png"),
                           width=200, height=40, stamp="a1",
                           targets=(((4, 4, 40, 18), Button("cancel|j1", "Cancel", "Drop it")),))
        (tmp_path / "origenerator_portrait_hud.json").write_text(
            hud_text(HudModel(funestra="portrait", foot=foot)), encoding="utf-8")

        feed.publish(_hosted(BridgeState()))

        assert parse_hud((tmp_path / "portrait_hud.json").read_text(encoding="utf-8")).foot == foot

    def test_a_satellite_the_hosted_app_has_not_filled_wears_the_mode_alone(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        (tmp_path / "origenerator_landscape_hud.json").write_text("", encoding="utf-8")

        feed.publish(_hosted(BridgeState()))

        for funestra in ("portrait", "landscape"):
            published = parse_hud((tmp_path / f"{funestra}_hud.json").read_text(encoding="utf-8"))
            assert published.corner is None
            assert published.seeds == ()
            assert published.title == "Origenerator mode"
            crown = ["portrait_crown"] if funestra == "portrait" else []
            assert [[button.command for button in row] for row in published.rows] == [
                [f"{funestra}_kino_activate", f"{funestra}_origenerator_activate",
                 f"{funestra}_minimize", *crown]]

    def test_a_satellite_switched_to_origenerator_keeps_its_whole_kino_hud_until_the_app_takes_it(
            self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        asked = BridgeState(origenerator_ready=True).with_satellite(
            FunTimeFunestra.PORTRAIT, mode=SatellitesMode.ORIGENERATOR)

        feed.publish(asked)

        portrait = hud(tmp_path, "portrait")
        assert portrait["corner"]["path"] == "C:/v/p.mp4"
        assert [(button["command"], bool(button.get("lit"))) for button in portrait["rows"][0][:2]] == [
            ("portrait_kino_activate", True), ("portrait_origenerator_activate", False)]

    def test_a_satellite_handed_back_wears_the_kino_hud_of_the_video_it_comes_home_to_at_once(
            self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        status = tmp_path / "portrait_status.txt"
        publish_satellite_status(status, "C:/v/p.mp4")
        feed.publish(BridgeState())
        away = _hosted(BridgeState(), "portrait")
        publish_satellite_status(status, "C:/g/scene one.png")
        feed.publish(away)

        comes_home_to = feed.the_video_it_comes_home_to(FunTimeFunestra.PORTRAIT)
        feed.publish(BridgeState())

        assert comes_home_to == "C:/v/p.mp4"
        assert hud(tmp_path, "portrait")["corner"]["path"] == "C:/v/p.mp4"
        publish_satellite_status(status, "C:/v/p.mp4")
        feed.publish(BridgeState(active_funestra=2))
        assert hud(tmp_path, "portrait")["corner"]["path"] == "C:/v/p.mp4"

    def test_a_satellite_on_its_way_home_keeps_the_apps_last_hud_once_the_app_lets_go(
            self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        hosted_file = tmp_path / "origenerator_portrait_hud.json"
        hosted_file.write_text(hud_text(HudModel(funestra="portrait", title="Locked")),
                               encoding="utf-8")
        away = _hosted(BridgeState(), "portrait")
        feed.publish(away)
        hosted_file.write_text("", encoding="utf-8")

        feed.publish(away.with_satellite(FunTimeFunestra.PORTRAIT, mode=SatellitesMode.KINO))

        assert hud(tmp_path, "portrait")["title"] == "Locked"

    def test_a_satellite_in_kino_mode_beside_a_hosted_one_keeps_its_own_map(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4")

        feed.publish(_hosted(BridgeState(origenerator_ready=True), "portrait"))

        portrait, landscape = (parse_hud((tmp_path / f"{funestra}_hud.json").read_text(
            encoding="utf-8")) for funestra in ("portrait", "landscape"))
        assert portrait.title == "Origenerator mode"
        assert [(button.command, button.lit) for button in landscape.rows[0][:2]] == [
            ("landscape_kino_activate", True), ("landscape_origenerator_activate", False)]
        assert landscape.corner.path == "C:/v/l.mp4"

    @pytest.mark.parametrize(("hosting", "hosted_satellites"), [
        (False, ()), (True, ()), (True, ("portrait", "landscape"))])
    def test_no_funestra_in_a_headset_session_offers_to_minimize(
        self, tmp_path, hosting, hosted_satellites,
    ):
        config = hosting_config(tmp_path) if hosting else make_config(tmp_path)
        feed = make_feed(tmp_path, config=replace(config, vr_main_funestra=True))

        state = BridgeState()
        for satellite in hosted_satellites:
            state = _hosted(state, satellite)
        feed.publish(state)

        published = [hud(tmp_path, "portrait"), hud(tmp_path, "landscape"), console(tmp_path)]
        commands = [button.get("command", "") for each in published for row in each["rows"]
                    for button in row]
        assert commands
        assert [command for command in commands if command.endswith("_minimize")] == []

    def test_a_hosted_hud_caught_mid_write_leaves_the_last_one_up(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        hosted_file = tmp_path / "origenerator_portrait_hud.json"
        hosted_file.write_text(hud_text(HudModel(funestra="portrait", title="Locked")),
                               encoding="utf-8")
        state = _hosted(BridgeState())
        feed.publish(state)
        real_read = Path.read_text

        def busy(path, *args, **kwargs):
            if path == hosted_file:
                raise PermissionError("being replaced")
            return real_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", busy):
            feed.publish(replace(state, active_funestra=3))

        assert hud(tmp_path, "portrait")["title"] == "Locked"

    def test_what_the_hosted_app_says_about_the_image_on_screen_reaches_the_funestra(
        self, tmp_path,
    ):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        (tmp_path / "origenerator_portrait_hud.json").write_text(
            hud_text(HudModel(funestra="portrait", item_note="Enhancing…")), encoding="utf-8")

        feed.publish(_hosted(BridgeState()))

        assert hud(tmp_path, "portrait")["item_note"] == "Enhancing…"

    def test_a_hosted_app_still_booting_is_published_so_the_button_can_dim(
        self, tmp_path,
    ):
        feed = make_feed(tmp_path, config=make_config(
            tmp_path, origenerator_enabled=True,
            origenerator_cmd_file=tmp_path / "origenerator_cmd.txt"))
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4")

        def origenerator_button() -> dict:
            return next(button for row in hud(tmp_path, "portrait")["rows"]
                        for button in row
                        if button["command"] == "portrait_origenerator_activate")

        feed.publish(BridgeState())
        assert origenerator_button().get("dim") is True

        feed.publish(BridgeState(origenerator_ready=True))
        assert not origenerator_button().get("dim")

    def test_the_published_hud_says_when_that_satellites_f_mode_is_on(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        for funestra in ("portrait", "landscape"):
            publish_satellite_status(tmp_path / f"{funestra}_status.txt",
                                     f"C:/v/{funestra}.mp4")
        state = replace(state, portrait=SatelliteState(favorites_filter=True))

        feed.publish(state)

        portrait = hud(tmp_path, "portrait")
        landscape = hud(tmp_path, "landscape")
        assert portrait["title"] == "Unlocked · Shuffle · F-Mode"
        assert f_mode_button(portrait)["lit"] is True
        assert landscape["title"] == "Unlocked · Shuffle"
        assert not f_mode_button(landscape).get("lit")

    def test_a_satellite_at_every_default_draws_its_reset_faded(self, tmp_path):
        feed = make_feed(tmp_path)
        for satellite in ("portrait", "landscape"):
            publish_satellite_status(tmp_path / f"{satellite}_status.txt", f"C:/v/{satellite}.mp4")

        feed.publish(replace(BridgeState(), portrait=SatelliteState(locked=True)))

        assert not reset_button(hud(tmp_path, "portrait")).get("dim")
        assert reset_button(hud(tmp_path, "landscape"))["dim"] is True

    def test_a_satellite_playing_off_normal_speed_has_a_reset_to_offer(self, tmp_path):
        feed = make_feed(tmp_path)
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/portrait.mp4",
                                 speed=1.5)
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/landscape.mp4")

        feed.publish(BridgeState())

        assert not reset_button(hud(tmp_path, "portrait")).get("dim")
        assert reset_button(hud(tmp_path, "landscape"))["dim"] is True

    def test_a_satellites_skip_ahead_is_pressable_only_with_the_osr2_and_a_funscripted_video(
            self, tmp_path):
        feed = make_feed(tmp_path)
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/portrait.mp4",
                                 has_funscript=True)
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/landscape.mp4",
                                 has_funscript=True)

        feed.publish(BridgeState(osr2_funestra=FunTimeFunestra.PORTRAIT))

        assert not skip_ahead(hud(tmp_path, "portrait")).get("dim")
        assert skip_ahead(hud(tmp_path, "landscape"))["dim"] is True

        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/portrait.mp4")
        feed.publish(BridgeState(osr2_funestra=FunTimeFunestra.PORTRAIT))

        assert skip_ahead(hud(tmp_path, "portrait"))["dim"] is True

    def test_the_main_funestras_skip_ahead_is_pressable_only_with_the_osr2_and_a_funscript(
            self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "main_player_status.txt").write_text("has_funscript=1\n", encoding="utf-8")

        feed.publish(BridgeState(osr2_funestra=FunTimeFunestra.MAIN))
        held = skip_ahead(console(tmp_path))
        feed.publish(BridgeState(osr2_funestra=FunTimeFunestra.LANDSCAPE))

        assert not held.get("dim")
        assert skip_ahead(console(tmp_path))["dim"] is True

    def test_a_satellite_on_a_video_of_ten_seconds_or_shorter_dims_its_ten_second_steps(
            self, tmp_path):
        feed = make_feed(tmp_path)
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/portrait.mp4",
                                 duration_ms=10_000)
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/landscape.mp4",
                                 duration_ms=10_001)

        feed.publish(BridgeState())

        assert [step.get("dim") for step in ten_second_steps(hud(tmp_path, "portrait"))] == [
            True, True]
        assert [step.get("dim") for step in ten_second_steps(hud(tmp_path, "landscape"))] == [
            None, None]

    def test_the_versions_button_lights_for_a_video_the_library_has_twice(self, tmp_path):
        media_root = tmp_path / "AI"
        upscale = (media_root / "2_outbox" / "upscaled_by_orientation" / "portrait"
                   / "provider" / "clip_topaz.mp4")
        original = media_root / "1_sorted" / "provider" / "portrait" / "clip.mp4"
        for path in (upscale, original):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")
        feed = make_feed(tmp_path, config=make_config(tmp_path, regen_media_root=media_root))
        publish_satellite_status(tmp_path / "portrait_status.txt", str(upscale))
        publish_satellite_status(tmp_path / "landscape_status.txt", str(original))

        feed.publish(BridgeState())

        assert not version_button(hud(tmp_path, "portrait")).get("dim")
        assert version_button(hud(tmp_path, "landscape"))["dim"] is True


    def test_a_hosted_shows_reset_is_never_faded_by_the_session(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        hosted = HudModel(funestra="portrait", title="Unlocked \u00b7 Shuffle",
                          rows=((Button("portrait_reset", "R", "Reset the show"),),))
        (tmp_path / "origenerator_portrait_hud.json").write_text(
            hud_text(hosted), encoding="utf-8")
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")

        feed.publish(_hosted(BridgeState()))

        assert not reset_button(hud(tmp_path, "portrait")).get("dim")

    def test_the_published_hud_says_which_satellite_has_the_floor(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        for funestra in ("portrait", "landscape"):
            publish_satellite_status(tmp_path / f"{funestra}_status.txt",
                                     f"C:/v/{funestra}.mp4")

        def actives(slot: int) -> tuple[bool, bool]:
            feed.publish(replace(state, active_funestra=slot))
            return tuple(hud(tmp_path, funestra)["active"]
                         for funestra in ("portrait", "landscape"))

        assert actives(2) == (True, False)
        assert actives(3) == (False, True)
        assert actives(1) == (False, False)

    def test_the_main_hud_says_when_the_primary_has_the_floor(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()

        def active(slot: int) -> bool:
            feed.publish(replace(state, active_funestra=slot))
            return console(tmp_path)["active"]

        assert active(1) is True
        assert active(2) is False

    def test_the_main_hud_says_what_has_the_osr2_and_the_osr2_part_whether_the_broker_is_up(
            self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")

        feed.publish(state)

        published = console(tmp_path)
        assert broker_is_up(dashboard(tmp_path))
        assert published["osr2"] in ("off", "auto", "funscript", "genau", "idle")

    def test_the_main_hud_says_the_osr2_is_off_while_a_video_still_drives_it_gone_quiet(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_serial_rx.txt").write_text("100.0", encoding="utf-8")
        (tmp_path / "osr2_serial_tx.txt").write_text("199.0", encoding="utf-8")
        (tmp_path / "main_player_status.txt").write_text(
            "video=C:/v/m.mp4\nhas_funscript=1\nfunscript_resting=0\n", encoding="utf-8")

        with file_channel_clock_at(200.0):
            feed.publish(BridgeState(main_mode=MainMode.KINO))

        assert console(tmp_path)["osr2"] == Osr2State.OFF

    def test_the_main_hud_names_an_osr2_the_broker_cannot_find(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_not_found.txt").write_text(str(time.time()), encoding="utf-8")

        feed.publish(BridgeState())

        assert console(tmp_path)["osr2"] == Osr2State.UNPLUGGED

    def test_a_broker_waiting_for_an_unplugged_osr2_is_lit_as_running(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_not_found.txt").write_text(str(time.time()), encoding="utf-8")

        feed.publish(BridgeState())

        assert broker_is_up(dashboard(tmp_path))

    def test_both_broker_lights_read_the_brokers_directory_not_the_sessions(self, tmp_path):
        broker_state = tmp_path / "primary_state"
        broker_state.mkdir()
        feed = make_feed(tmp_path, config=make_config(
            tmp_path,
            broker_state_dir=broker_state,
        ))
        state = BridgeState()

        def published(state_dir: Path) -> tuple[dict, dict]:
            now = time.time()
            (state_dir / "broker_heartbeat.txt").write_text(str(now), encoding="utf-8")
            (state_dir / "osr2_serial_rx.txt").write_text(str(now), encoding="utf-8")
            feed.publish(state)
            return console(tmp_path), dashboard(tmp_path)

        session, session_part = published(tmp_path)
        assert not broker_is_up(session_part)
        assert session["osr2"] == "off"

        broker, broker_part = published(broker_state)
        assert broker_is_up(broker_part)
        assert broker["osr2"] != "off"

    def test_the_main_huds_lock_names_genaus_pace_off_its_drive_readout(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState(main_mode=MainMode.GENAU)
        publish_drive(feed.config.genau_drive_file,
                      DriveHud(speed=50, amplitude=60, center=50, advance_interval=7))

        feed.publish(state)

        lock = next(button for row in console(tmp_path)["rows"] for button in row
                    if button["command"] == "main_lock")
        assert "every 7s" in lock["tooltip"]

    def test_the_main_hud_carries_the_lock_back_to_whoever_draws_it(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        main_funestra = feed.config.main_funestra_status_file
        genau = feed.config.state_dir / "genau_status.txt"

        def published(mode: str) -> bool:
            feed.publish(replace(state, main_mode=mode))
            return console(tmp_path)["locked"]

        main_funestra.write_text("video=C:/v/n.mp4\nlocked=0\n", encoding="utf-8")
        genau.write_text("locked=1\n", encoding="utf-8")
        assert published("kino") is False
        assert published("genau") is True

        main_funestra.write_text("video=C:/v/n.mp4\nlocked=1\n", encoding="utf-8")
        genau.write_text("locked=0\n", encoding="utf-8")
        assert published("kino") is True
        assert published("genau") is False

    def test_the_main_huds_f_mode_in_genau_mode_is_genaus_own(self, tmp_path):
        feed = make_feed(tmp_path)

        def f_mode_lit(state: BridgeState) -> bool:
            feed.publish(state)
            return next(bool(button.get("lit")) for row in console(tmp_path)["rows"] for button in row
                        if button["command"] == "main_fmode")

        assert f_mode_lit(BridgeState(main_mode=MainMode.GENAU, genau_favorites_filter=True)) is True
        assert f_mode_lit(BridgeState(main_mode=MainMode.GENAU, main_scripted_filter=True)) is False

    def test_the_main_hud_offers_genaus_shapes_only_where_it_has_vr_videos(self, tmp_path):
        state = BridgeState(main_mode=MainMode.GENAU, genau_plays_vr=False)

        def shape_buttons(config) -> set[tuple[str, bool]]:
            make_feed(tmp_path, config=config).publish(state)
            return {(button["command"], bool(button.get("lit")))
                    for row in console(tmp_path)["rows"] for button in row
                    if button.get("command", "").startswith("main_projection")}

        assert shape_buttons(make_config(tmp_path)) == set()
        assert shape_buttons(make_config(tmp_path, genau_vr_flicks=str(tmp_path / "vr_flicks"))) == {
            ("main_projection_both", False), ("main_projection_none", True)}

    def test_the_main_huds_ten_second_steps_are_dim_on_a_video_of_ten_seconds_or_shorter(
            self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        status = feed.config.main_funestra_status_file

        status.write_text("video=C:/v/n.mp4\nduration_ms=9000\n", encoding="utf-8")
        feed.publish(state)
        short = [step.get("dim") for step in ten_second_steps(console(tmp_path))]
        status.write_text("video=C:/v/n.mp4\nduration_ms=90000\n", encoding="utf-8")
        feed.publish(state)
        longer = [step.get("dim") for step in ten_second_steps(console(tmp_path))]

        assert (short, longer) == ([True, True], [None, None])

    def test_the_main_huds_reset_is_faded_while_there_is_nothing_to_put_back(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        status = feed.config.main_funestra_status_file

        status.write_text("video=C:/v/n.mp4\nlocked=0\nspeed=1.0\nlength_mode=mixed\n",
                          encoding="utf-8")
        feed.publish(state)
        assert reset_button(console(tmp_path))["dim"] is True

        status.write_text("video=C:/v/n.mp4\nlocked=1\nspeed=1.0\nlength_mode=mixed\n",
                          encoding="utf-8")
        feed.publish(state)
        assert not reset_button(console(tmp_path)).get("dim")

    def test_each_satellites_hud_says_whether_its_own_video_is_a_favorite(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        feed.config.regen_metadata_root = tmp_path / "metadata"
        portrait, landscape = tmp_path / "v" / "p.mp4", tmp_path / "v" / "l.mp4"
        publish_satellite_status(tmp_path / "portrait_status.txt", str(portrait))
        publish_satellite_status(tmp_path / "landscape_status.txt", str(landscape))
        mark_favorite(portrait, feed.config.regen_metadata_root, True)

        feed.publish(state)

        portrait = hud(tmp_path, "portrait")
        landscape = hud(tmp_path, "landscape")
        assert portrait["is_favorite"] is True
        assert landscape["is_favorite"] is False

    def test_a_videos_record_is_re_read_only_when_it_moves(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        feed.config.regen_metadata_root = tmp_path / "metadata"
        video = tmp_path / "v" / "p.mp4"
        publish_satellite_status(tmp_path / "portrait_status.txt", str(video))
        record = metadata_path_for(video, feed.config.regen_metadata_root)
        record.parent.mkdir(parents=True)
        record.write_text('{"video": {}}', encoding="utf-8")

        with patch("fun_time.hud_feed.load_metadata", side_effect=load_metadata) as read:
            feed.publish(state)
            feed.publish(state)
            assert read.call_count == 1, "an unmoved record is not read again"

            mark_favorite(video, feed.config.regen_metadata_root, True)
            feed.publish(state)
            assert read.call_count == 2

        portrait = hud(tmp_path, "portrait")
        assert portrait["is_favorite"] is True

    def test_publishing_is_throttled_below_the_tick_rate(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")

        with patch.object(feed.publisher, "publish", return_value=True) as publish:
            feed.publish_due(state, now=100.0)
            feed.publish_due(state, now=100.0 + PUBLISH_INTERVAL_S / 2)
            assert publish.call_count == 2, "one publish per Funestra, once"

            feed.publish_due(state, now=100.0 + PUBLISH_INTERVAL_S)

        assert publish.call_count == 4

    def test_a_status_that_cannot_be_read_keeps_the_video_it_last_named(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        status = tmp_path / "portrait_status.txt"
        publish_satellite_status(status, "C:/v/p.mp4")
        feed.publish(state)

        status.write_text("", encoding="utf-8")
        feed.publish(state)

        portrait = hud(tmp_path, "portrait")
        assert portrait["corner"]["path"] == "C:/v/p.mp4"

    def test_a_status_that_cannot_be_read_keeps_the_speed_it_last_named(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        status = tmp_path / "portrait_status.txt"
        publish_satellite_status(status, "C:/v/p.mp4", speed=1.5)
        feed.publish(state)

        status.write_text("", encoding="utf-8")
        feed.publish(state)

        assert not reset_button(hud(tmp_path, "portrait")).get("dim")

    def test_a_satellite_that_has_not_started_yet_publishes_an_empty_hud(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()

        feed.publish(state)

        portrait = hud(tmp_path, "portrait")
        assert portrait["corner"] is None

    def test_a_session_without_a_publisher_publishes_nothing(self, tmp_path):
        HudFeed(config=make_config(tmp_path), publisher=None).publish(BridgeState())

        assert not (tmp_path / "portrait_hud.json").exists()
        assert not (tmp_path / "landscape_hud.json").exists()


def _published(tmp_path, funestra: str) -> HudModel:
    return parse_hud((tmp_path / f"{funestra}_hud.json").read_text(encoding="utf-8"))


def _osr2_commands(model: HudModel) -> list[str]:
    return [button.command for row in model.osr2_rows for button in row]


def _main_hud_osr2_commands(tmp_path) -> list[str]:
    return [button.get("command", "") for row in console(tmp_path)["osr2_rows"] for button in row]


def _dashboard_osr2(tmp_path) -> MainHudModel:
    return read_main_hud(osr2_part_path(tmp_path))


def _dashboard_osr2_commands(tmp_path) -> list[str]:
    return [button.command for row in _dashboard_osr2(tmp_path).osr2_rows for button in row]


class TestTheOsr2PartIsOnTheDashboard:
    def test_it_is_the_main_funestras_to_begin_with(self, tmp_path):
        make_feed(tmp_path).publish(BridgeState())

        assert _dashboard_osr2(tmp_path).funestra == "main"
        assert "robot_hand_park" in _dashboard_osr2_commands(tmp_path)

    def test_it_is_the_whole_section_of_the_satellite_funestra_that_takes_it(self, tmp_path):
        make_feed(tmp_path).publish(BridgeState(osr2_funestra=2, osr2_control=OSR2_PARKED))

        part = _dashboard_osr2(tmp_path)
        assert part.funestra == "portrait"
        assert {"robot_hand_toggle_learned", "robot_hand_release"} <= set(_dashboard_osr2_commands(tmp_path))
        assert (part.osr2, part.osr2_control) == (Osr2State.OFF, OSR2_PARKED)
        assert [button.command for button in part.osr2_controls] == ["broker_panel"]

    def test_its_aim_row_follows_genaus_own_switches(self, tmp_path):
        (tmp_path / "genau_status.txt").write_text("learned=1\nshape=sawtooth\n", encoding="utf-8")

        make_feed(tmp_path).publish(BridgeState())

        rows = {b.command: b for row in _dashboard_osr2(tmp_path).osr2_rows for b in row}
        assert rows["robot_hand_toggle_learned"].lit is True
        assert rows["robot_hand_cycle_shape"].tooltip == "Waveform: Sawtooth"

    def test_it_is_told_the_app_is_paused(self, tmp_path):
        make_feed(tmp_path).publish(BridgeState(osr2_funestra=2, omni_paused=True))

        assert _dashboard_osr2(tmp_path).paused is True

    def test_it_carries_the_max_intensity_on_its_line(self, tmp_path):
        make_feed(tmp_path).publish(BridgeState(osr2_funestra=2, max_intensity=35))

        assert _dashboard_osr2(tmp_path).max_intensity == 35

    def test_it_says_a_scripted_satellite_funestras_funscript_has_the_osr2(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_serial_rx.txt").write_text("100.0", encoding="utf-8")
        (tmp_path / "portrait_status.txt").write_text(
            "video=C:/v/p.mp4\nhas_funscript=1\nfunscript_resting=0\n", encoding="utf-8")

        with file_channel_clock_at(110.0):
            feed.publish(BridgeState(osr2_funestra=2))

        assert _dashboard_osr2(tmp_path).osr2 == Osr2State.FUNSCRIPT

    def test_it_names_why_a_satellite_funestras_osr2_cannot_be_heard(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_not_answering.txt").write_text(str(time.time()), encoding="utf-8")

        feed.publish(BridgeState(osr2_funestra=3))

        assert _dashboard_osr2(tmp_path).osr2 == Osr2State.NO_ANSWER

    def test_its_readout_is_composed_unless_genau_has_the_main_funestras_slot(self, tmp_path):
        feed = make_feed(tmp_path)

        feed.publish(BridgeState(main_mode=MainMode.GENAU))
        genau_on_the_main = _dashboard_osr2(tmp_path).main_mode
        feed.publish(BridgeState(main_mode=MainMode.GENAU, osr2_funestra=2))

        assert (genau_on_the_main, _dashboard_osr2(tmp_path).main_mode) == (MainMode.GENAU, MainMode.KINO)


class TestTheOsr2SectionIsOnTheFunestraThatHasTheOsr2:
    def test_the_satellite_funestra_that_takes_it_wears_only_its_osr2_button_lit(self, tmp_path):
        make_feed(tmp_path).publish(BridgeState(osr2_funestra=2, osr2_control=OSR2_PARKED))

        portrait = _published(tmp_path, "portrait")
        assert portrait.osr2_rows == ((osr2_button(FunTimeFunestra.PORTRAIT, has_it=True),),)
        assert (portrait.osr2_controls, portrait.max_intensity) == ((), None)

    def test_the_main_main_hud_has_it_to_begin_with_and_wears_its_button_lit(self, tmp_path):
        make_feed(tmp_path).publish(BridgeState())

        assert console(tmp_path)["has_osr2"] is True
        (button, *_jump), = console(tmp_path)["osr2_rows"]
        assert (button["command"], button.get("lit")) == ("main_take_osr2", True)
        for funestra in ("portrait", "landscape"):
            assert _osr2_commands(_published(tmp_path, funestra)) == [f"{funestra}_take_osr2"]
            assert _published(tmp_path, funestra).osr2 == ""

    def test_the_funestras_it_left_carry_the_button_that_takes_it(self, tmp_path):
        make_feed(tmp_path).publish(BridgeState(osr2_funestra=2))

        assert console(tmp_path)["has_osr2"] is False
        assert _main_hud_osr2_commands(tmp_path) == ["main_take_osr2"]
        assert _osr2_commands(_published(tmp_path, "landscape")) == ["landscape_take_osr2"]

    def test_a_satellite_funestra_whose_video_is_scripted_says_its_funscript_has_the_osr2(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_serial_rx.txt").write_text("100.0", encoding="utf-8")
        (tmp_path / "portrait_status.txt").write_text(
            "video=C:/v/p.mp4\nhas_funscript=1\nfunscript_resting=0\n", encoding="utf-8")

        with file_channel_clock_at(110.0):
            feed.publish(BridgeState(osr2_funestra=2))

        assert _published(tmp_path, "portrait").osr2 == Osr2State.FUNSCRIPT

    def test_a_satellite_the_hosted_app_has_wears_its_button_too(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))

        feed.publish(_hosted(BridgeState(osr2_funestra=3)))

        assert _published(tmp_path, "landscape").osr2_rows == (
            (osr2_button(FunTimeFunestra.LANDSCAPE, has_it=True),),)
        assert _published(tmp_path, "portrait").osr2_rows == (
            (osr2_button(FunTimeFunestra.PORTRAIT, has_it=False),),)

    def test_a_satellite_the_hosted_app_holds_is_told_whether_it_has_the_osr2_as_that_changes(
            self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))

        feed.publish(_hosted(BridgeState(osr2_funestra=3)))
        feed.publish(_hosted(BridgeState(osr2_funestra=3)))
        feed.publish(_hosted(BridgeState(osr2_funestra=2)))

        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8").split() == [
            "portrait_osr2|0", "landscape_osr2|1", "portrait_osr2|1", "landscape_osr2|0"]

    def test_a_satellite_back_in_origenerator_mode_is_told_again(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))

        feed.publish(_hosted(BridgeState(osr2_funestra=2), "portrait"))
        feed.publish(BridgeState(osr2_funestra=2))
        feed.publish(_hosted(BridgeState(osr2_funestra=2), "portrait"))

        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8").split() == [
            "portrait_osr2|1", "portrait_osr2|1"]


class TestASatelliteWithTheOsr2:
    def test_names_why_the_osr2_cannot_be_heard(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_not_answering.txt").write_text(str(time.time()), encoding="utf-8")

        feed.publish(BridgeState(osr2_funestra=3))

        assert _published(tmp_path, "landscape").osr2 == Osr2State.NO_ANSWER


class TestOsr2Mode:
    def test_a_device_never_heard_from_is_off(self, tmp_path):
        assert make_feed(tmp_path).osr2_mode() is Osr2Mode.OFF

    def test_a_device_that_has_gone_quiet_is_off_whatever_is_still_sent_to_it(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_serial_rx.txt").write_text("100.0", encoding="utf-8")
        (tmp_path / "osr2_serial_tx.txt").write_text("199.0", encoding="utf-8")

        with file_channel_clock_at(200.0):
            assert feed.osr2_mode() is Osr2Mode.OFF

    def test_one_taking_commands_is_controlled(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_serial_rx.txt").write_text("100.0", encoding="utf-8")

        with file_channel_clock_at(110.0):
            assert feed.osr2_mode() is Osr2Mode.CONTROLLED

    def test_one_running_its_own_auto_mode_is_auto(self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "osr2_serial_rx.txt").write_text("100.0", encoding="utf-8")
        (tmp_path / "broker_mode.txt").write_text("1", encoding="utf-8")

        with file_channel_clock_at(110.0):
            assert feed.osr2_mode() is Osr2Mode.AUTO


def test_the_main_huds_crown_goes_dark_once_the_app_gives_it_to_the_portrait_funestra(tmp_path):
    feed = make_feed(tmp_path)

    feed.publish(BridgeState(crowned=Crown.PORTRAIT))

    crown = next(button for row in console(tmp_path)["rows"] for button in row
                 if button["command"] == "main_crown")
    assert not crown.get("lit")


def test_the_portrait_huds_crown_is_lit_once_the_app_gives_it_the_crown(tmp_path):
    feed = make_feed(tmp_path)

    feed.publish(BridgeState(crowned=Crown.PORTRAIT))

    crown = next(button for row in hud(tmp_path, "portrait")["rows"] for button in row
                 if button["command"] == "portrait_crown")
    assert crown.get("lit")


class TestWhatTheHudSaysAboutWhereItSits:
    def test_each_hud_carries_its_own_corner_and_collapse(self, tmp_path):
        feed = make_feed(tmp_path)
        state = replace(
            BridgeState(),
            portrait=SatelliteState(hud_corner=HudCorner.LOWER_RIGHT, hud_minimized=True),
            main_hud_corner=HudCorner.UPPER_RIGHT, main_hud_minimized=True)

        feed.publish(state)

        assert hud(tmp_path, "portrait")["hud_corner"] == HudCorner.LOWER_RIGHT
        assert hud(tmp_path, "portrait")["hud_minimized"] is True
        assert hud(tmp_path, "landscape")["hud_corner"] == HudCorner.UPPER_LEFT
        assert hud(tmp_path, "landscape")["hud_minimized"] is False
        assert console(tmp_path)["hud_corner"] == HudCorner.UPPER_RIGHT
        assert console(tmp_path)["hud_minimized"] is True

    def test_a_satellite_the_hosted_app_holds_wears_the_sessions_own_corner(self, tmp_path):
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        hosted = hud_text(HudModel(funestra="portrait", title="Slideshow",
                                   corner=HudCell(path="C:/p/one.png")))
        (tmp_path / "origenerator_portrait_hud.json").write_text(hosted, encoding="utf-8")
        state = _hosted(replace(
            BridgeState(origenerator_ready=True),
            portrait=SatelliteState(hud_corner=HudCorner.LOWER_LEFT, hud_minimized=True)))

        feed.publish(state)

        assert hud(tmp_path, "portrait")["hud_corner"] == HudCorner.LOWER_LEFT
        assert hud(tmp_path, "portrait")["hud_minimized"] is True
