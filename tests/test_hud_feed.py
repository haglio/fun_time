"""What the players draw: each satellite's lock map, and the main console.

The feed's inputs are the session's config, the bridge state and a publisher,
so these build one and ask it — no dispatch loop, no windows, no tick.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from player_core.console import OSR2_CONTROL_OFF
from player_core.hud_button import Button
from player_core.satellite_hud import HudCell, HudModel, hud_text, parse_hud

from fun_time.bridge_records import BridgeConfig
from fun_time.hud_feed import PUBLISH_INTERVAL_S, HudFeed
from fun_time.hud_transport import HudPublisher
from fun_time.shared_state import BridgeState, SideState


def make_config(tmp_path, **overrides) -> BridgeConfig:
    settings = dict(
        portrait_cmd_file=tmp_path / "portrait_cmd.txt",
        portrait_paused_file=tmp_path / "portrait_paused.txt",
        portrait_status_file=tmp_path / "portrait_status.txt",
        portrait_playlist_file=tmp_path / "portrait_playlist.tsv",
        landscape_cmd_file=tmp_path / "landscape_cmd.txt",
        landscape_paused_file=tmp_path / "landscape_paused.txt",
        landscape_status_file=tmp_path / "landscape_status.txt",
        landscape_playlist_file=tmp_path / "landscape_playlist.tsv",
        favs_file=tmp_path / "favs.txt",
        weird_dir=tmp_path / "weird",
        state_dir=tmp_path,
        main_sources="",
        portrait_sources="",
        landscape_sources="",
        genau_mode_file=tmp_path / "rh_mode.txt",
        genau_cmd_file=tmp_path / "rh_cmd.txt",
        genau_paused_file=tmp_path / "rh_paused.txt",
        audio_paused_file=tmp_path / "audio_paused.txt",
        audio_volume_file=tmp_path / "audio_volume.txt",
        main_player_cmd_file=tmp_path / "main_player_cmd.txt",
        main_player_paused_file=tmp_path / "main_player_paused.txt",
        main_player_status_file=tmp_path / "main_player_status.txt",
        dashboard_state_file=tmp_path / "dashboard_state.ini",
        broker_heartbeat_file=tmp_path / "broker_heartbeat.txt",
    )
    settings.update(overrides)
    return BridgeConfig(**settings)


def hosting_config(tmp_path) -> BridgeConfig:
    """A session hosting an Origenerator, with the files that app publishes
    each side's panel to."""
    return make_config(
        tmp_path, origenerator_enabled=True,
        origenerator_cmd_file=tmp_path / "origenerator_cmd.txt",
        portrait_origenerator_hud_file=tmp_path / "origenerator_portrait_hud.json",
        landscape_origenerator_hud_file=tmp_path / "origenerator_landscape_hud.json")


def make_feed(tmp_path, *, config=None) -> HudFeed:
    publisher = HudPublisher(
        {"portrait": tmp_path / "portrait_hud.json",
         "landscape": tmp_path / "landscape_hud.json",
         "main_player": tmp_path / "main_player_console.json"},
        tmp_path / "thumbs",
    )
    return HudFeed(config=config or make_config(tmp_path), publisher=publisher)


def publish_satellite_status(path: Path, video, *, fraction: float = 0.1) -> None:
    path.write_text(
        f"video={video}\nposition_ms={round(fraction * 1000)}\nduration_ms=1000\n"
        "paused=0\nlocked=0\n",
        encoding="utf-8",
    )


def panel(tmp_path, side: str) -> dict:
    return json.loads((tmp_path / f"{side}_hud.json").read_text(encoding="utf-8"))


def console(tmp_path) -> dict:
    return json.loads((tmp_path / "main_player_console.json").read_text(encoding="utf-8"))


def f_mode_button(published: dict) -> dict:
    """The F-mode button a published satellite panel declares, as written."""
    return next(button for row in published["rows"] for button in row
                if button["action"].endswith("_fmode"))


def reset_button(published: dict) -> dict:
    """The reset button a published panel declares, as written."""
    return next(button for row in published["rows"] for button in row
                if button["action"].endswith("reset"))


def broker_is_up(published: dict) -> bool:
    """What the published console's OSR2-line control says of the broker: lit
    while the service runs, red (``warn``) while it is down."""
    (broker,) = published["osr2_controls"]
    assert broker["action"] == "broker_panel"
    return bool(broker.get("lit")) and not broker.get("warn")


class TestHudPublishing:
    """Each satellite's lock map and the main console, built from the session's
    config and the bridge state and published to the files the players read."""

    def test_each_satellites_panel_carries_its_own_clip_and_lock(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4")
        state = replace(state, portrait=SideState(locked=True, filter="alpha"))

        feed.publish(state)

        portrait = panel(tmp_path, "portrait")
        landscape = panel(tmp_path, "landscape")
        assert portrait["side"] == "portrait"
        assert portrait["locked"] is True
        # The status line composes the lot — lock, order, and the filter unlabeled.
        assert portrait["lock_label"] == "Locked · Shuffle · alpha"
        assert portrait["corner"]["path"] == "C:/v/p.mp4"
        assert landscape["locked"] is False
        assert landscape["corner"]["path"] == "C:/v/l.mp4"

    def test_the_console_is_told_what_the_room_does_to_the_osr2(self, tmp_path):
        """The state is the session's; the player drawing the console has no way
        to know it, which is why it is published like the rest of the room."""
        feed = make_feed(tmp_path)

        feed.publish(replace(BridgeState(), osr2_control=OSR2_CONTROL_OFF))

        assert console(tmp_path)["osr2_control"] == OSR2_CONTROL_OFF

    def test_a_side_the_hosted_app_has_wears_that_apps_own_panel(self, tmp_path):
        """The hosted app publishes the side's map, line and buttons; the
        session puts its own row over them -- the way back to its videos, and
        minimize for the player's window -- and says which side has the floor."""
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        hosted = HudModel(
            side="portrait", lock_label="Unlocked · Shuffle",
            corner=HudCell(path="C:/g/scene one.png"),
            seeds=(HudCell(path="C:/g/scene two.png"),), seed_count=2,
            rows=((Button("portrait_next", "N", "Next slide"),),),
        )
        (tmp_path / "origenerator_portrait_hud.json").write_text(
            hud_text(hosted), encoding="utf-8")

        feed.publish(BridgeState(satellites_mode="origenerator", active_side=2))

        portrait = parse_hud((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        assert portrait.corner.path == "C:/g/scene one.png"
        assert portrait.seeds == hosted.seeds
        assert portrait.lock_label == "Unlocked · Shuffle"
        assert portrait.active is True
        assert [button.action for button in portrait.rows[0]] == [
            "satellites_video_activate", "origenerator_activate", "portrait_minimize"]
        assert [button.lit for button in portrait.rows[0][:2]] == [False, True]
        assert [button.action for button in portrait.rows[1]] == ["portrait_next"]

    def test_a_side_the_hosted_app_has_not_filled_wears_the_mode_alone(self, tmp_path):
        """Between the switch and the app's first panel -- and after it lets a
        side go -- there is nothing of the app's to draw: the side names the
        mode, with the way back on it, and no map of the videos it left."""
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        (tmp_path / "origenerator_landscape_hud.json").write_text("", encoding="utf-8")

        feed.publish(BridgeState(satellites_mode="origenerator"))

        for side in ("portrait", "landscape"):
            published = parse_hud((tmp_path / f"{side}_hud.json").read_text(encoding="utf-8"))
            assert published.corner is None
            assert published.seeds == ()
            assert published.lock_label == "Origenerator mode"
            assert [[button.action for button in row] for row in published.rows] == [
                ["satellites_video_activate", "origenerator_activate", f"{side}_minimize"]]

    def test_a_hosted_panel_caught_mid_write_leaves_the_last_one_up(self, tmp_path):
        """A read that loses to the app's own republish is not the app letting
        go: the side keeps the panel it had rather than flashing the mode's."""
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        hosted_file = tmp_path / "origenerator_portrait_hud.json"
        hosted_file.write_text(hud_text(HudModel(side="portrait", lock_label="Locked")),
                               encoding="utf-8")
        state = BridgeState(satellites_mode="origenerator")
        feed.publish(state)
        real_read = Path.read_text

        def busy(path, *args, **kwargs):
            if path == hosted_file:
                raise PermissionError("being replaced")
            return real_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", busy):
            feed.publish(replace(state, active_side=3))

        assert panel(tmp_path, "portrait")["lock_label"] == "Locked"

    def test_a_hosted_app_still_booting_is_published_so_the_button_can_dim(
        self, tmp_path,
    ):
        """The HUDs draw the mode pair, so they are where a mode that cannot be
        entered yet has to show — dim, and posting nothing.  What they cannot
        work out for themselves is whether the app is up: only the loop reads
        its status file, so the answer rides out on the button it declares."""
        feed = make_feed(tmp_path, config=make_config(
            tmp_path, origenerator_enabled=True,
            origenerator_cmd_file=tmp_path / "origenerator_cmd.txt"))
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4")

        def origenerator_button() -> dict:
            return next(button for row in panel(tmp_path, "portrait")["rows"]
                        for button in row if button["action"] == "origenerator_activate")

        feed.publish(BridgeState())
        assert origenerator_button().get("dim") is True

        feed.publish(BridgeState(origenerator_ready=True))
        assert not origenerator_button().get("dim")

    def test_the_published_panel_says_when_that_sides_f_mode_is_on(self, tmp_path):
        """The flag lives on the bridge state and nowhere the player can see, so the
        publish is the only way F-mode reaches the screen a satellite is on — as the
        status line, and as the flag its own button lights off.

        It is sided: the satellite that is not in F-mode must not say it is."""
        feed, state = make_feed(tmp_path), BridgeState()
        for side in ("portrait", "landscape"):
            publish_satellite_status(tmp_path / f"{side}_status.txt",
                                     f"C:/v/{side}.mp4")
        state = replace(state, portrait=SideState(f_mode=True))

        feed.publish(state)

        portrait = panel(tmp_path, "portrait")
        landscape = panel(tmp_path, "landscape")
        assert portrait["lock_label"] == "Unlocked · Shuffle · F-Mode"
        assert f_mode_button(portrait)["lit"] is True
        assert landscape["lock_label"] == "Unlocked · Shuffle"
        assert not f_mode_button(landscape).get("lit")

    def test_a_side_at_every_default_draws_its_reset_faded(self, tmp_path):
        """Pressing it would change nothing, so the button says as much rather
        than offering an act that does nothing."""
        feed = make_feed(tmp_path)
        for side in ("portrait", "landscape"):
            publish_satellite_status(tmp_path / f"{side}_status.txt", f"C:/v/{side}.mp4")

        feed.publish(replace(BridgeState(), portrait=SideState(locked=True)))

        assert not reset_button(panel(tmp_path, "portrait")).get("dim")
        assert reset_button(panel(tmp_path, "landscape"))["dim"] is True

    def test_a_hosted_shows_reset_is_never_faded_by_the_session(self, tmp_path):
        """That reset belongs to the show, whose state this room cannot see, so
        it stays pressable however settled the player underneath it is."""
        feed = make_feed(tmp_path, config=hosting_config(tmp_path))
        hosted = HudModel(side="portrait", lock_label="Unlocked \u00b7 Shuffle",
                          rows=((Button("portrait_reset", "R", "Reset the show"),),))
        (tmp_path / "origenerator_portrait_hud.json").write_text(
            hud_text(hosted), encoding="utf-8")
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")

        feed.publish(BridgeState(satellites_mode="origenerator"))

        assert not reset_button(panel(tmp_path, "portrait")).get("dim")

    def test_the_published_panel_says_which_side_has_the_floor(self, tmp_path):
        """The active side is a slot number in the state and a side *name* on the
        panel, so exactly one satellite can claim it — and neither does while the
        the main player holds it."""
        feed, state = make_feed(tmp_path), BridgeState()
        for side in ("portrait", "landscape"):
            publish_satellite_status(tmp_path / f"{side}_status.txt",
                                     f"C:/v/{side}.mp4")

        def actives(slot: int) -> tuple[bool, bool]:
            feed.publish(replace(state, active_side=slot))
            return tuple(panel(tmp_path, side)["active"]
                         for side in ("portrait", "landscape"))

        assert actives(2) == (True, False)
        assert actives(3) == (False, True)
        assert actives(1) == (False, False)

    def test_the_console_says_when_the_primary_has_the_floor(self, tmp_path):
        """The main player's dot rides the console file, the same file that carries the
        rest of its panel — one source for both players, in place of the separate
        command it used to be sent."""
        feed, state = make_feed(tmp_path), BridgeState()

        def active(slot: int) -> bool:
            feed.publish(replace(state, active_side=slot))
            return console(tmp_path)["active"]

        assert active(1) is True   # the main player holds it
        assert active(2) is False  # a satellite does

    def test_the_console_says_what_has_the_osr2_and_whether_the_broker_is_up(self, tmp_path):
        """Broker status is the main player's alone — it moved off the dashboard onto
        this panel — and the OSR2 state comes down as one word for the console to
        badge."""
        feed, state = make_feed(tmp_path), BridgeState()
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")

        feed.publish(state)

        published = console(tmp_path)
        assert broker_is_up(published)
        assert published["osr2"] in ("off", "auto", "funscript", "genau", "idle")

    def test_both_broker_lights_read_the_brokers_directory_not_the_sessions(self, tmp_path):
        """A branch session moves ``state/`` into its worktree; the machine's one
        broker keeps writing where it always did.

        Reading the heartbeat and the serial stamp out of the *session's*
        directory is what had this console calling a live broker dead and a
        driven OSR2 off — so a stamp in the session's own directory must not
        light either one.
        """
        broker_state = tmp_path / "primary_state"
        broker_state.mkdir()
        feed = make_feed(tmp_path, config=make_config(
            tmp_path,
            broker_state_dir=broker_state,
            broker_heartbeat_file=broker_state / "broker_heartbeat.txt",
        ))
        state = BridgeState()

        def published(state_dir: Path) -> dict:
            now = time.time()
            (state_dir / "broker_heartbeat.txt").write_text(str(now), encoding="utf-8")
            (state_dir / "osr2_serial_rx.txt").write_text(str(now), encoding="utf-8")
            feed.publish(state)
            return console(tmp_path)

        # The worktree's own state dir: fresh stamps nothing reads.
        session = published(tmp_path)
        assert not broker_is_up(session)
        assert session["osr2"] == "off"

        broker = published(broker_state)
        assert broker_is_up(broker)
        assert broker["osr2"] != "off"

    def test_the_consoles_lock_names_genaus_pace_off_its_drive_readout(self, tmp_path):
        """In genau mode the lock says how long an unheld clip stays up, and the
        one place Genau says its pace is the drive readout it publishes."""
        from player_core.drive_readout import DriveHud, publish_drive

        feed, state = make_feed(tmp_path), BridgeState(main_mode="genau")
        publish_drive(feed.config.genau_drive_file,
                      DriveHud(speed=50, amplitude=60, center=50, advance_interval=7))

        feed.publish(state)

        lock = next(button for row in console(tmp_path)["rows"] for button in row
                    if button["action"] == "main_lock")
        assert "every 7s" in lock["tooltip"]

    def test_the_console_carries_the_lock_back_to_whoever_draws_it(self, tmp_path):
        """Each player owns its own lock, and neither can see the other's — so
        both go out on their status files and the one the mode says is showing
        comes back down here, the way the loop state does."""
        feed, state = make_feed(tmp_path), BridgeState()
        main_player = feed.config.main_player_status_file
        genau = feed.config.state_dir / "genau_status.txt"

        def published(mode: str) -> bool:
            feed.publish(replace(state, main_mode=mode))
            return console(tmp_path)["locked"]

        main_player.write_text("video=C:/v/n.mp4\nlocked=0\n", encoding="utf-8")
        genau.write_text("locked=1\n", encoding="utf-8")
        assert published("video") is False
        assert published("genau") is True

        main_player.write_text("video=C:/v/n.mp4\nlocked=1\n", encoding="utf-8")
        genau.write_text("locked=0\n", encoding="utf-8")
        assert published("video") is True
        assert published("genau") is False

    def test_the_consoles_reset_is_faded_while_there_is_nothing_to_put_back(self, tmp_path):
        feed, state = make_feed(tmp_path), BridgeState()
        status = feed.config.main_player_status_file

        status.write_text("video=C:/v/n.mp4\nlocked=0\nspeed=1.0\nlength_mode=mixed\n",
                          encoding="utf-8")
        feed.publish(state)
        assert reset_button(console(tmp_path))["dim"] is True

        status.write_text("video=C:/v/n.mp4\nlocked=1\nspeed=1.0\nlength_mode=mixed\n",
                          encoding="utf-8")
        feed.publish(state)
        assert not reset_button(console(tmp_path)).get("dim")

    def test_each_sides_panel_says_whether_its_own_clip_is_a_favorite(self, tmp_path):
        """The dashboard's panel used to say this by turning green; the HUD marks
        it, so the loop has to judge each side's clip against the favs file."""
        feed, state = make_feed(tmp_path), BridgeState()
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        publish_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4")
        feed.config.favs_file.write_text("local,C:/v/p.mp4,web\n", encoding="utf-8")

        feed.publish(state)

        portrait = panel(tmp_path, "portrait")
        landscape = panel(tmp_path, "landscape")
        assert portrait["is_favorite"] is True
        assert landscape["is_favorite"] is False

    def test_the_favorites_file_is_re_read_only_when_it_moves(self, tmp_path):
        """Every publish asks the question, ~7x a second for the whole session,
        while the list itself moves a few times an hour — so the read is gated on
        the file actually having changed, and picks the change up when it does."""
        feed, state = make_feed(tmp_path), BridgeState()
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")
        favs = feed.config.favs_file
        favs.write_text("local,C:/v/other.mp4,web\n", encoding="utf-8")

        with patch("fun_time.hud_feed.read_favs_content",
                   side_effect=lambda path: path.read_text(encoding="utf-8")) as read:
            feed.publish(state)
            feed.publish(state)
            assert read.call_count == 1, "an unmoved file is not read again"

            favs.write_text("local,C:/v/p.mp4,web\nlocal,C:/v/other.mp4,web\n", encoding="utf-8")
            feed.publish(state)
            assert read.call_count == 2

        portrait = panel(tmp_path, "portrait")
        assert portrait["is_favorite"] is True

    def test_publishing_is_throttled_below_the_tick_rate(self, tmp_path):
        """The loop ticks 20x/s; rebuilding and rewriting both panels that often
        is waste the map never shows, so publishing runs on its own cadence."""
        feed, state = make_feed(tmp_path), BridgeState()
        publish_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4")

        with patch.object(feed.publisher, "publish", return_value=True) as publish:
            feed.publish_due(state, now=100.0)
            feed.publish_due(state, now=100.0 + PUBLISH_INTERVAL_S / 2)
            assert publish.call_count == 2, "one publish per side, once"

            feed.publish_due(state, now=100.0 + PUBLISH_INTERVAL_S)

        assert publish.call_count == 4

    def test_a_status_that_cannot_be_read_keeps_the_clip_it_last_named(self, tmp_path):
        """A satellite always has a clip, so a status file that reads blank means
        the read lost — not that the player has nothing.

        Believing the blank republishes an empty panel, which the player renders
        as a HUD with nothing on it, and the next tick puts it back: the map
        blinks.  It is most visible under OmniPause, where the picture is frozen
        and the map is the only thing that can move.
        """
        feed, state = make_feed(tmp_path), BridgeState()
        status = tmp_path / "portrait_status.txt"
        publish_satellite_status(status, "C:/v/p.mp4")
        feed.publish(state)

        status.write_text("", encoding="utf-8")  # caught mid-republish
        feed.publish(state)

        portrait = panel(tmp_path, "portrait")
        assert portrait["corner"]["path"] == "C:/v/p.mp4"

    def test_a_satellite_that_has_not_started_yet_publishes_an_empty_panel(self, tmp_path):
        # The other side of it: before a satellite's first status there is no
        # clip to hold onto, and an empty map is the truth.
        feed, state = make_feed(tmp_path), BridgeState()

        feed.publish(state)

        portrait = panel(tmp_path, "portrait")
        assert portrait["corner"] is None

    def test_a_session_without_a_publisher_publishes_nothing(self, tmp_path):
        """FunTimeVR and the integration runs bring no HUD files with them."""
        HudFeed(config=make_config(tmp_path), publisher=None).publish(BridgeState())

        assert not (tmp_path / "portrait_hud.json").exists()
        assert not (tmp_path / "landscape_hud.json").exists()


class TestOsr2Mode:
    """What the main console is told the OSR2 is doing — this feed's payload
    for the main player is the only consumer of the answer."""

    def test_osr2_mode_off_when_rx_file_missing(self, tmp_path):
        feed = make_feed(tmp_path)
        # No osr2_serial_rx.txt exists

        assert feed.osr2_mode() == "off"

    def test_osr2_mode_off_when_rx_timestamp_stale(self, tmp_path):
        feed = make_feed(tmp_path)
        rx_file = tmp_path / "osr2_serial_rx.txt"
        rx_file.write_text("100.0", encoding="utf-8")

        with patch("app_support.file_channel.time") as mock_time:
            mock_time.time.return_value = 200.0  # 100s stale
            assert feed.osr2_mode() == "off"

    def test_osr2_mode_controlled_when_device_on(self, tmp_path):
        feed = make_feed(tmp_path)
        rx_file = tmp_path / "osr2_serial_rx.txt"
        rx_file.write_text("100.0", encoding="utf-8")

        with patch("app_support.file_channel.time") as mock_time:
            mock_time.time.return_value = 110.0  # 10s ago — fresh
            assert feed.osr2_mode() == "controlled"

    def test_osr2_mode_auto_when_device_on_and_genau(self, tmp_path):
        feed = make_feed(tmp_path)
        rx_file = tmp_path / "osr2_serial_rx.txt"
        rx_file.write_text("100.0", encoding="utf-8")
        (tmp_path / "rh_mode.txt").write_text("1", encoding="utf-8")

        with patch("app_support.file_channel.time") as mock_time:
            mock_time.time.return_value = 110.0
            assert feed.osr2_mode() == "auto"
