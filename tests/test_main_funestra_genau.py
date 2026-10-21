"""Genau as it runs on the Main Funestra: its own channel, its own picture, its
own status, over a fabricated flicks folder whose decode hands back numbered frames."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np
from funestra.console_hud import ModeHud
from funestra.funestra import User
from funestra.modes import NoticeLevel

from fun_time import genau_engine
from fun_time.arrivals import LOOK_EVERY_S
from fun_time.content import Noun
from fun_time.genau_config import GenauSettings
from fun_time.media_actions import ensure_in_favs
from main_funestra.genau import Genau, GenauChannels
from main_funestra.genau_verbs import genau_f_mode


class FakeSink:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    def send(self, command: str) -> None:
        self.sent.append(command)

    def close(self) -> None:
        self.closed = True


class FakeNotifier:
    def __init__(self) -> None:
        self.flicks: list[Path] = []
        self.visible: list[bool] = []
        self.closed = False

    def notify_flick(self, path: Path) -> None:
        self.flicks.append(path)

    def notify_visible(self, is_visible: bool) -> None:
        self.visible.append(is_visible)

    def close(self) -> None:
        self.closed = True


class FakeNotices:
    def __init__(self) -> None:
        self.said: list[tuple[str, NoticeLevel]] = []

    def say(self, message: str, *, level: NoticeLevel) -> None:
        self.said.append((message, level))


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _frames(count: int = 8, width: int = 16, height: int = 8) -> list[np.ndarray]:
    return [np.full((height, width, 3), i, dtype=np.uint8) for i in range(count)]


def _run_now(*, target, args=(), name=""):
    """The loader's thread runs on the spot; the UDP reader, which would bind a
    port and loop, is not started at all."""
    if name == "genau-udp":
        return
    target(*args)


class _CondemningInTheBackground:
    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def __call__(self, *, target, args=(), name=""):
        if name != "genau-condemn":
            _run_now(target=target, args=args, name=name)
            return
        thread = threading.Thread(target=target, args=args, name=name)
        self.threads.append(thread)
        thread.start()


class World:
    def __init__(self, tmp_path: Path, *, flicks=("alpha one.mp4", "beta two.mp4", "gamma three.mp4"),
                 favorites=(), f_mode=False, start_flick=None, latest=False, metadata_root=None,
                 start_thread=_run_now) -> None:
        self.favs_file = tmp_path / "favs.csv"
        self.flicks_folder = tmp_path / "flicks"
        self.flat_dir = self.flicks_folder / "2D" / "non_AI"
        self.flat_dir.mkdir(parents=True)
        for arrived, name in enumerate(flicks, start=1):
            flick = self.flat_dir / name
            flick.write_bytes(b"flick")
            os.utime(flick, (1_000_000_000 + arrived, 1_000_000_000 + arrived))
        for name in favorites:
            ensure_in_favs(self.favs_file, str(self.flat_dir / name))
        state = tmp_path / "state"
        state.mkdir()
        self.channels = GenauChannels(
            command=state / "genau_cmd.txt", paused=state / "genau_paused.txt",
            status=state / "genau_status.txt", drive=state / "genau_drive.txt")
        self.sink = FakeSink()
        self.notifier = FakeNotifier()
        self.notices = FakeNotices()
        self.clock = Clock()
        self.genau = Genau(
            flicks_folder=self.flicks_folder,
            settings=GenauSettings(shuffle_on_load=False),
            channels=self.channels,
            notifier=self.notifier,
            notices=self.notices,
            tcode_sink=self.sink,
            start_flick=start_flick,
            latest=latest,
            favs_file=self.favs_file,
            f_mode=f_mode,
            metadata_root=metadata_root,
            decode=lambda _path: _frames(),
            start_thread=start_thread,
            clock=self.clock,
            log=logging.getLogger("test.genau"),
        )

    def send(self, line: str) -> None:
        self.channels.command.write_text(line + "\n", encoding="utf-8")
        self.genau.tick()
        self.genau.tick()

    def tick(self, seconds: float = 0.05) -> None:
        self.clock.now += seconds
        self.genau.tick()

    def status(self) -> dict[str, str]:
        lines = self.channels.status.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)


def test_genau_is_a_thing_a_funestra_runs(tmp_path):
    assert isinstance(World(tmp_path).genau, User)


class TestTheFlickOnScreen:
    def test_it_opens_on_the_first_flick_of_the_folder_and_tells_the_audio_companion(self, tmp_path):
        world = World(tmp_path)

        assert world.genau.top_block() == ModeHud(video="alpha one")
        assert world.notifier.flicks == [world.flat_dir / "alpha one.mp4"]

    def test_its_verbs_arrive_on_its_own_channel_and_never_on_the_funestras(self, tmp_path):
        world = World(tmp_path)

        assert world.genau.apply_command("NEXT") is False
        world.send("NEXT")

        assert world.genau.top_block() == ModeHud(video="beta two")

    def test_it_publishes_its_own_status_and_adds_no_line_to_the_funestras(self, tmp_path):
        world = World(tmp_path)

        world.tick()

        assert world.status()["flick"] == str(world.flat_dir / "alpha one.mp4")
        assert world.genau.status_fields() == {}

    def test_its_status_says_when_human_inspired_motion_has_the_hand_and_nothing_of_cruise(self, tmp_path):
        world = World(tmp_path)

        world.send("LEARNED_ON")

        assert world.status()["learned"] == "1"
        assert "cruise" not in world.status()

    def test_weird_moves_the_flick_to_the_pile_beside_the_folder(self, tmp_path):
        world = World(tmp_path)

        world.send("WEIRD")

        assert (tmp_path / "weird" / "2D" / "non_AI" / "alpha one.mp4").is_file()
        assert world.genau.top_block() == ModeHud(video="beta two")

    def test_weird_moves_a_flick_still_being_read_once_it_is_let_go(self, tmp_path):
        condemning = _CondemningInTheBackground()
        world = World(tmp_path, start_thread=condemning)
        flick = world.flat_dir / "alpha one.mp4"

        with flick.open("rb"):
            world.send("WEIRD")
            assert flick.is_file()
        for thread in condemning.threads:
            thread.join(5)

        assert (tmp_path / "weird" / "2D" / "non_AI" / "alpha one.mp4").is_file()
        assert not flick.exists()

    def test_weird_gives_up_on_a_flick_that_stays_open_and_says_so(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(genau_engine, "CONDEMN_PATIENCE_S", 0.5)
        condemning = _CondemningInTheBackground()
        world = World(tmp_path, start_thread=condemning)
        flick = world.flat_dir / "alpha one.mp4"

        with flick.open("rb"), caplog.at_level(logging.ERROR, logger="test.genau"):
            world.send("WEIRD")
            for thread in condemning.threads:
                thread.join(5)

        assert flick.is_file()
        assert "alpha one.mp4 was still open" in caplog.text

    def test_latest_rescans_the_folder_newest_first(self, tmp_path):
        world = World(tmp_path)
        newest = world.flat_dir / "delta four.mp4"
        newest.write_bytes(b"flick")
        os.utime(newest, (4_000_000_000, 4_000_000_000))

        world.send("LATEST")

        assert world.genau.top_block() == ModeHud(video="delta four")


class TestWhatArrivesInItsFolder:
    def test_under_latest_it_comes_up_after_the_flick_on_screen(self, tmp_path):
        world = World(tmp_path, latest=True)
        world.genau.set_showing(True)

        (world.flat_dir / "delta four.mp4").write_bytes(b"flick")
        world.tick(LOOK_EVERY_S)
        world.send("NEXT")

        assert world.genau.top_block().video == "delta four"

    def test_under_shuffle_it_waits_its_turn(self, tmp_path):
        world = World(tmp_path)
        world.genau.set_showing(True)

        (world.flat_dir / "delta four.mp4").write_bytes(b"flick")
        world.tick(LOOK_EVERY_S)
        world.send("NEXT")

        assert world.genau.top_block().video == "beta two"

    def test_while_kino_has_the_window_it_waits_until_genau_has_it_again(self, tmp_path):
        world = World(tmp_path, latest=True)

        (world.flat_dir / "delta four.mp4").write_bytes(b"flick")
        world.tick(LOOK_EVERY_S)
        world.send("NEXT")
        moved_on_to_out_of_sight = world.genau.top_block().video
        world.genau.set_showing(True)
        world.tick(LOOK_EVERY_S)
        world.send("NEXT")

        assert (moved_on_to_out_of_sight, world.genau.top_block().video) == (
            "beta two", "delta four")

    def test_in_f_mode_only_a_favorite_comes_up_next(self, tmp_path):
        world = World(tmp_path, latest=True, favorites=("alpha one.mp4", "beta two.mp4"),
                      f_mode=True)
        world.genau.set_showing(True)

        (world.flat_dir / "delta four.mp4").write_bytes(b"flick")
        world.tick(LOOK_EVERY_S)
        world.send("NEXT")

        assert world.genau.top_block().video == "alpha one"


class TestFMode:
    def test_it_keeps_only_the_favorites(self, tmp_path):
        world = World(tmp_path)
        ensure_in_favs(world.favs_file, str(world.flat_dir / "gamma three.mp4"))

        assert world.genau.apply_command(genau_f_mode(True)) is True
        world.send("NEXT")

        assert world.genau.top_block().video == "gamma three"

    def test_off_it_brings_every_flick_back_and_keeps_the_one_on_screen(self, tmp_path):
        world = World(tmp_path)
        ensure_in_favs(world.favs_file, str(world.flat_dir / "gamma three.mp4"))
        world.genau.apply_command(genau_f_mode(True))

        world.genau.apply_command(genau_f_mode(False))
        assert world.genau.top_block().video == "gamma three"
        world.send("NEXT")

        assert world.genau.top_block().video == "alpha one"

    def test_the_status_line_says_f_mode_while_it_is_on(self, tmp_path):
        world = World(tmp_path)
        ensure_in_favs(world.favs_file, str(world.flat_dir / "gamma three.mp4"))

        world.genau.apply_command(genau_f_mode(True))

        assert world.genau.top_block() == ModeHud(video="gamma three", scripted_filter=True)

    def test_with_no_favorites_it_holds_the_flick_on_screen_and_says_no_other_is_included(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr("fun_time.content.genau_flick_noun", lambda: Noun(one="pip", many="pips"))
        world = World(tmp_path)
        world.send("LOCK_OFF")

        world.genau.apply_command(genau_f_mode(True))
        world.tick()

        assert world.genau.top_block().video == "alpha one"
        assert world.status()["locked"] == "1"
        assert world.notices.said == [("Including no other pips", NoticeLevel.WARNING)]

    def test_it_opens_in_f_mode_when_the_room_kept_it_on(self, tmp_path):
        world = World(tmp_path, favorites=("beta two.mp4",), f_mode=True)

        assert world.genau.top_block() == ModeHud(video="beta two", scripted_filter=True)

    def test_opening_in_f_mode_with_no_favorites_holds_the_first_flick_and_says_so(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr("fun_time.content.genau_flick_noun", lambda: Noun(one="pip", many="pips"))

        world = World(tmp_path, f_mode=True)
        world.tick()

        assert world.genau.top_block().video == "alpha one"
        assert world.status()["locked"] == "1"
        assert world.notices.said == [("Including no other pips", NoticeLevel.WARNING)]


class TestItsPicture:
    def test_the_frame_the_motion_chose_is_the_picture_counted_up(self, tmp_path):
        world = World(tmp_path)
        world.send("RESUME")
        shown = set()
        for _ in range(40):
            world.tick(0.05)
            picture = world.genau.picture()
            if picture.frame is not None:
                shown.add(int(picture.frame[0, 0, 0]))
                assert picture.count == 8
                assert 0 <= picture.played < 8

        assert len(shown) > 1

    def test_a_press_on_its_dial_moves_the_device(self, tmp_path):
        world = World(tmp_path)
        world.send("RESUME")
        world.tick(0.05)
        sent_before = len(world.sink.sent)

        world.genau.picture().seek_loop(0.5)
        world.tick(0.05)

        assert len(world.sink.sent) > sent_before

    def test_the_picture_carries_the_flicks_time_on_screen(self, tmp_path):
        """Genau opens with its flick held and nothing elapsed of the ten
        seconds a flick gets."""
        world = World(tmp_path)
        world.tick(0.05)

        picture = world.genau.picture()

        assert (picture.elapsed_ms, picture.interval_ms) == (0.0, 10_000.0)

    def test_a_press_along_its_track_puts_the_flick_that_far_into_its_time(self, tmp_path):
        world = World(tmp_path)
        world.tick(0.05)

        world.genau.picture().seek_time(7_000.0)

        assert world.genau.picture().elapsed_ms == 7_000.0

    def test_what_is_still_decoding_is_said_on_the_picture(self, tmp_path):
        world = World(tmp_path)

        assert world.genau.picture().loading is None


class TestWhoHasTheWindow:
    def test_shown_it_tells_the_audio_companion_it_is_visible_and_hidden_that_it_is_not(self, tmp_path):
        world = World(tmp_path)

        world.genau.set_showing(True)
        world.tick()
        world.genau.set_showing(False)
        world.tick()

        assert world.notifier.visible[-2:] == [True, False]


def test_closing_it_closes_the_device_line_and_the_audio_companion(tmp_path):
    world = World(tmp_path)

    world.genau.close()

    assert (world.sink.closed, world.notifier.closed) == (True, True)
