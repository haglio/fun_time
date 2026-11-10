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
from funestra.modes import MainMode, Osr2State
from funestra.osr2_visualizer import DriveHud, publish_drive
from funestra.satellite_hud import HudCell, HudModel, PaintedFoot, hud_text, parse_hud

from fun_time.bridge_records import BridgeConfig
from fun_time.crown import Crown
from fun_time.dispatch_loop_setup import LOOP_TICK_S
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.hud_feed import PUBLISH_INTERVAL_S, HudFeed
from fun_time.hud_transport import HudPublisher
from fun_time.osr2_section import DASHBOARD_OSR2_PART, osr2_button, osr2_part_path
from fun_time.shared_state import BridgeState, SatelliteState
from tests.bridge_configs import made_up_bridge_config


def make_config(tmp_path, **overrides) -> BridgeConfig:
    return made_up_bridge_config(tmp_path, **overrides)


def _hosted(state: BridgeState, *funestras: str) -> BridgeState:
    for funestra in funestras or ("portrait", "landscape"):
        state = state.with_satellite(FunTimeFunestra[funestra.upper()])
    return state


def make_feed(tmp_path, *, config=None) -> HudFeed:
    publisher = HudPublisher(
        {"portrait": tmp_path / "portrait_hud.json",
         "landscape": tmp_path / "landscape_hud.json",
         "main_funestra": tmp_path / "main_player_console.json",
         DASHBOARD_OSR2_PART: osr2_part_path(tmp_path)},
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


def reset_button(published: dict) -> dict:
    return next(button for row in published["rows"] for button in row
                if button["command"].endswith("reset"))


def skip_ahead(published: dict) -> dict:
    return next(button for row in published["rows"] for button in row
                if button.get("command", "").endswith("_funscript_jump"))


def ten_second_steps(published: dict) -> list[dict]:
    return [button for row in published["rows"] for button in row
            if button.get("command", "").endswith(("_nudge_prev", "_nudge_next"))]


def broker_is_up(published: dict) -> bool:
    (broker,) = published["osr2_controls"]
    assert broker["command"] == "broker_panel"
    return bool(broker.get("lit")) and not broker.get("warn")


class TestHudPublishing:
    def test_a_satellite_the_hosted_app_has_wears_that_apps_own_hud(self, tmp_path):
        feed = make_feed(tmp_path, config=make_config(tmp_path))
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
            "portrait_minimize", "portrait_crown"]
        assert [button.command for button in portrait.rows[1]] == ["portrait_next"]

    def test_core_hidden_behind_the_browser_shows_its_job_queue_on_the_satellites_hud(self, tmp_path):
        feed = make_feed(tmp_path, config=make_config(tmp_path))
        foot = PaintedFoot(file=str(tmp_path / "origenerator_portrait_hud_foot_a1.png"),
                           width=200, height=40, stamp="a1",
                           targets=(((4, 4, 40, 18), Button("cancel|j1", "Cancel", "Drop it")),))
        (tmp_path / "origenerator_portrait_hud.json").write_text(
            hud_text(HudModel(funestra="portrait", foot=foot)), encoding="utf-8")

        feed.publish(_hosted(BridgeState()))

        assert parse_hud((tmp_path / "portrait_hud.json").read_text(encoding="utf-8")).foot == foot

    def test_a_satellite_the_hosted_app_has_not_filled_wears_its_own_buttons_alone(self, tmp_path):
        feed = make_feed(tmp_path, config=make_config(tmp_path))
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        (tmp_path / "origenerator_landscape_hud.json").write_text("", encoding="utf-8")

        feed.publish(_hosted(BridgeState()))

        for funestra in ("portrait", "landscape"):
            published = parse_hud((tmp_path / f"{funestra}_hud.json").read_text(encoding="utf-8"))
            assert published.corner is None
            assert published.seeds == ()
            assert published.title == ""
            crown = ["portrait_crown"] if funestra == "portrait" else []
            assert [[button.command for button in row] for row in published.rows] == [
                [f"{funestra}_minimize", *crown]]

    def test_a_satellite_the_app_holds_keeps_the_apps_last_hud_over_an_empty_read(
            self, tmp_path):
        feed = make_feed(tmp_path, config=make_config(tmp_path))
        hosted_file = tmp_path / "origenerator_portrait_hud.json"
        hosted_file.write_text(hud_text(HudModel(funestra="portrait", title="Locked")),
                               encoding="utf-8")
        away = _hosted(BridgeState(), "portrait")
        feed.publish(away)
        hosted_file.write_text("", encoding="utf-8")

        feed.publish(away)

        assert hud(tmp_path, "portrait")["title"] == "Locked"

    @pytest.mark.parametrize("hosted_satellites", [(), ("portrait", "landscape")])
    def test_no_funestra_in_a_headset_session_offers_to_minimize(self, tmp_path, hosted_satellites):
        feed = make_feed(tmp_path, config=replace(make_config(tmp_path), vr_main_funestra=True))

        state = BridgeState()
        for satellite in hosted_satellites:
            state = _hosted(state, satellite)
        feed.publish(state)

        published = [hud(tmp_path, "portrait"), hud(tmp_path, "landscape"), console(tmp_path)]
        commands = [button.get("command", "") for each in published for row in each["rows"]
                    for button in row]
        assert commands
        assert [command for command in commands if command.endswith("_minimize")] == []

    def test_losing_a_read_to_the_apps_republish_is_not_the_app_letting_go(self, tmp_path):
        feed = make_feed(tmp_path, config=make_config(tmp_path))
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
        feed = make_feed(tmp_path, config=make_config(tmp_path))
        (tmp_path / "origenerator_portrait_hud.json").write_text(
            hud_text(HudModel(funestra="portrait", item_note="Enhancing…")), encoding="utf-8")

        feed.publish(_hosted(BridgeState()))

        assert hud(tmp_path, "portrait")["item_note"] == "Enhancing…"


    def test_the_main_funestras_skip_ahead_is_pressable_only_with_the_osr2_and_a_funscript(
            self, tmp_path):
        feed = make_feed(tmp_path)
        (tmp_path / "main_player_status.txt").write_text("has_funscript=1\n", encoding="utf-8")

        feed.publish(BridgeState(osr2_funestra=FunTimeFunestra.MAIN))
        held = skip_ahead(console(tmp_path))
        feed.publish(BridgeState(osr2_funestra=FunTimeFunestra.LANDSCAPE))

        assert not held.get("dim")
        assert skip_ahead(console(tmp_path))["dim"] is True


    def test_the_session_never_fades_a_reset_belonging_to_the_hosted_show(self, tmp_path):
        feed = make_feed(tmp_path, config=make_config(tmp_path))
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

    def test_broker_lights_read_the_machines_one_brokers_folder_not_a_branch_sessions(self, tmp_path):
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

    def test_the_main_huds_lock_comes_off_the_showing_funestras_own_status_file(self, tmp_path):
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

    @pytest.mark.parametrize(("state", "vr_folders"), [
        (BridgeState(main_plays_vr=False), "vr_library_dirs"),
        (BridgeState(main_mode=MainMode.GENAU, genau_plays_vr=False), "genau_vr_flicks"),
    ], ids=["kino", "genau"])
    def test_the_main_hud_offers_shapes_only_where_its_mode_has_vr_videos(
            self, tmp_path, state, vr_folders):
        def shape_buttons(config) -> set[tuple[str, bool]]:
            make_feed(tmp_path, config=config).publish(state)
            return {(button["command"], bool(button.get("lit")))
                    for row in console(tmp_path)["rows"] for button in row
                    if button.get("command", "").startswith("main_projection")}

        assert shape_buttons(make_config(tmp_path)) == set()
        assert shape_buttons(make_config(tmp_path, **{vr_folders: str(tmp_path / "vr")})) == {
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


    def test_publishing_keeps_up_with_five_second_videos_below_the_tick_rate(self, tmp_path):
        a_satellites_video_s = 5.0
        assert LOOP_TICK_S < PUBLISH_INTERVAL_S < a_satellites_video_s / 20
        feed, state = make_feed(tmp_path), BridgeState()

        with patch.object(feed.publisher, "publish_text", return_value=True) as publish:
            feed.publish_due(state, now=100.0)
            feed.publish_due(state, now=100.0 + PUBLISH_INTERVAL_S / 2)
            assert publish.call_count == 4, "one publish per Funestra and the OSR2 part, once"

            feed.publish_due(state, now=100.0 + PUBLISH_INTERVAL_S)

        assert publish.call_count == 8


    def test_a_satellite_that_has_not_started_yet_publishes_an_empty_hud(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()

        feed.publish(state)

        portrait = hud(tmp_path, "portrait")
        assert portrait["corner"] is None

    def test_a_session_with_the_dashboard_off_publishes_no_hud(self, tmp_path):
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
        feed = make_feed(tmp_path, config=make_config(tmp_path))

        feed.publish(_hosted(BridgeState(osr2_funestra=3)))

        assert _published(tmp_path, "landscape").osr2_rows == (
            (osr2_button(FunTimeFunestra.LANDSCAPE, has_it=True),),)
        assert _published(tmp_path, "portrait").osr2_rows == (
            (osr2_button(FunTimeFunestra.PORTRAIT, has_it=False),),)

    def test_the_hosted_apps_skip_ahead_is_told_the_osr2_holder_once_per_change(self, tmp_path):
        feed = make_feed(tmp_path, config=make_config(tmp_path))

        feed.publish(_hosted(BridgeState(osr2_funestra=3)))
        feed.publish(_hosted(BridgeState(osr2_funestra=3)))
        feed.publish(_hosted(BridgeState(osr2_funestra=2)))

        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8").split() == [
            "portrait_osr2|0", "landscape_osr2|1", "portrait_osr2|1", "landscape_osr2|0"]

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
    def test_each_hud_carries_the_corner_and_collapse_the_sessions_keys_gave_it(self, tmp_path):
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
        feed = make_feed(tmp_path, config=make_config(tmp_path))
        hosted = hud_text(HudModel(funestra="portrait", title="Slideshow",
                                   corner=HudCell(path="C:/p/one.png")))
        (tmp_path / "origenerator_portrait_hud.json").write_text(hosted, encoding="utf-8")
        state = _hosted(replace(
            BridgeState(origenerator_ready=True),
            portrait=SatelliteState(hud_corner=HudCorner.LOWER_LEFT, hud_minimized=True)))

        feed.publish(state)

        assert hud(tmp_path, "portrait")["hud_corner"] == HudCorner.LOWER_LEFT
        assert hud(tmp_path, "portrait")["hud_minimized"] is True
