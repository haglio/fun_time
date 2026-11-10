from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from app_support.flick_folder import trash_folder_in
from funestra.funestra import User
from funestra.main_hud import ModeHud
from funestra.modes import MessageLevel

from fun_time import genau_engine
from fun_time.arrivals import LOOK_EVERY_S
from fun_time.content import Noun
from fun_time.genau_config import GenauSettings
from fun_time.inclusion_pairs import BOTH
from fun_time.media_metadata import mark_favorite, metadata_path_for
from main_funestra.genau import (
    Genau,
    GenauChannels,
    GenauFlicks,
    GenauOutputs,
    GenauSeams,
)
from main_funestra.genau_verbs import genau_f_mode, genau_orientations
from tests.genau_fakes import (
    FakeNotifier,
    FakeSink,
    every_thread_now_but_the_port_binding_udp_reader,
)


class FakeMessages:
    def __init__(self) -> None:
        self.said: list[tuple[str, MessageLevel]] = []

    def say(self, message: str, *, level: MessageLevel) -> None:
        self.said.append((message, level))


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _frames(count: int = 8, width: int = 16, height: int = 8) -> list[np.ndarray]:
    return [np.full((height, width, 3), i, dtype=np.uint8) for i in range(count)]


class _CondemningInTheBackground:
    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def __call__(self, *, target, args=(), name=""):
        if name != "genau-condemn":
            every_thread_now_but_the_port_binding_udp_reader(target=target, args=args, name=name)
            return
        thread = threading.Thread(target=target, args=args, name=name)
        self.threads.append(thread)
        thread.start()


@dataclass(frozen=True)
class WorldSetup:
    flicks: tuple = ("alpha one.mp4", "beta two.mp4", "gamma three.mp4")
    favorites: tuple = ()
    f_mode: bool = False
    start_flick: str | None = None
    latest: bool = False
    metadata_root: Path | None = None
    turned: tuple = ()
    orientations: tuple = BOTH
    start_thread: Callable = every_thread_now_but_the_port_binding_udp_reader
    origenerator_commands: Path | None = None


class World:
    def __init__(self, tmp_path: Path, setup: WorldSetup | None = None) -> None:
        setup = setup or WorldSetup()
        flicks, favorites, turned = setup.flicks, setup.favorites, setup.turned
        self.metadata_root = tmp_path / "metadata" if setup.metadata_root is None else setup.metadata_root
        self.flicks_folder = tmp_path / "flicks"
        self.flat_dir = self.flicks_folder / "2D" / "non_AI"
        self.flat_dir.mkdir(parents=True)
        for arrived, name in enumerate(flicks, start=1):
            flick = self.flat_dir / name
            flick.write_bytes(b"flick")
            os.utime(flick, (1_000_000_000 + arrived, 1_000_000_000 + arrived))
        for name, orientation in turned:
            sidecar = metadata_path_for(self.flat_dir / name, self.metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps({"video": {"orientation": orientation}}),
                               encoding="utf-8")
        for name in favorites:
            mark_favorite(self.flat_dir / name, self.metadata_root, True)
        state = tmp_path / "state"
        state.mkdir()
        self.channels = GenauChannels(
            command=state / "genau_cmd.txt", paused=state / "genau_paused.txt",
            status=state / "genau_status.txt", drive=state / "genau_drive.txt")
        self.sink = FakeSink()
        self.notifier = FakeNotifier()
        self.messages = FakeMessages()
        self.clock = Clock()
        self.genau = Genau(
            GenauFlicks(
                folder=self.flicks_folder,
                start_flick=setup.start_flick,
                latest=setup.latest,
                f_mode=setup.f_mode,
                orientations=setup.orientations,
                metadata_root=self.metadata_root,
            ),
            settings=GenauSettings(shuffle_on_load=False),
            channels=self.channels,
            outputs=GenauOutputs(notifier=self.notifier, tcode_sink=self.sink,
                                 messages=self.messages,
                                 origenerator_commands=setup.origenerator_commands),
            seams=GenauSeams(
                decode=lambda _path: _frames(),
                start_thread=setup.start_thread,
                clock=self.clock,
                log=logging.getLogger("test.genau"),
            ),
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

    def test_trash_moves_the_flick_to_the_pile_beside_the_folder(self, tmp_path):
        world = World(tmp_path)

        world.send("TRASH")

        assert (trash_folder_in(tmp_path) / "2D" / "non_AI" / "alpha one.mp4").is_file()
        assert world.genau.top_block() == ModeHud(video="beta two")

    def test_trash_moves_a_flick_still_being_read_once_it_is_let_go(self, tmp_path):
        condemning = _CondemningInTheBackground()
        world = World(tmp_path, WorldSetup(start_thread=condemning))
        flick = world.flat_dir / "alpha one.mp4"

        with flick.open("rb"):
            world.send("TRASH")
            assert flick.is_file()
        for thread in condemning.threads:
            thread.join(5)

        assert (trash_folder_in(tmp_path) / "2D" / "non_AI" / "alpha one.mp4").is_file()
        assert not flick.exists()

    def test_trash_tells_a_hosted_origenerator_where_the_flick_went(self, tmp_path):
        commands = tmp_path / "origenerator_cmd.txt"
        world = World(tmp_path, WorldSetup(origenerator_commands=commands))

        world.send("TRASH")

        landed = trash_folder_in(tmp_path) / "2D" / "non_AI" / "alpha one.mp4"
        assert commands.read_text(encoding="utf-8").splitlines() == [
            f"TRASH_FILE|{world.flat_dir / 'alpha one.mp4'}|{landed}"]

    def test_trash_tells_origenerator_of_a_flick_still_being_read_once_it_is_let_go(self, tmp_path):
        commands = tmp_path / "origenerator_cmd.txt"
        condemning = _CondemningInTheBackground()
        world = World(tmp_path, WorldSetup(start_thread=condemning,
                                           origenerator_commands=commands))

        with (world.flat_dir / "alpha one.mp4").open("rb"):
            world.send("TRASH")
            told_while_open = commands.exists()
        for thread in condemning.threads:
            thread.join(5)

        assert not told_while_open
        assert commands.read_text(encoding="utf-8").startswith("TRASH_FILE|")

    def test_trash_gives_up_on_a_flick_that_stays_open_and_says_so(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(genau_engine, "CONDEMN_PATIENCE_S", 0.5)
        condemning = _CondemningInTheBackground()
        world = World(tmp_path, WorldSetup(start_thread=condemning))
        flick = world.flat_dir / "alpha one.mp4"

        with flick.open("rb"), caplog.at_level(logging.ERROR, logger="test.genau"):
            world.send("TRASH")
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
        world = World(tmp_path, WorldSetup(latest=True))
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
        world = World(tmp_path, WorldSetup(latest=True))

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
        world = World(tmp_path, WorldSetup(latest=True, favorites=("alpha one.mp4", "beta two.mp4"), f_mode=True))
        world.genau.set_showing(True)

        (world.flat_dir / "delta four.mp4").write_bytes(b"flick")
        world.tick(LOOK_EVERY_S)
        world.send("NEXT")

        assert world.genau.top_block().video == "alpha one"

    def test_with_landscape_left_out_only_a_portrait_flick_comes_up_next(self, tmp_path):
        world = World(tmp_path, WorldSetup(latest=True, metadata_root=tmp_path / "metadata", turned=(("alpha one.mp4", "portrait"), ("beta two.mp4", "portrait")), orientations=(True, False)))
        world.genau.set_showing(True)

        (world.flat_dir / "delta four.mp4").write_bytes(b"flick")
        world.tick(LOOK_EVERY_S)
        world.send("NEXT")

        assert world.genau.top_block().video == "alpha one"


class TestFMode:
    def test_it_keeps_only_the_favorites(self, tmp_path):
        world = World(tmp_path)
        mark_favorite(world.flat_dir / "gamma three.mp4", world.metadata_root, True)

        assert world.genau.apply_command(genau_f_mode(True)) is True
        world.send("NEXT")

        assert world.genau.top_block().video == "gamma three"

    def test_off_it_brings_every_flick_back_and_keeps_the_one_on_screen(self, tmp_path):
        world = World(tmp_path)
        mark_favorite(world.flat_dir / "gamma three.mp4", world.metadata_root, True)
        world.genau.apply_command(genau_f_mode(True))

        world.genau.apply_command(genau_f_mode(False))
        assert world.genau.top_block().video == "gamma three"
        world.send("NEXT")

        assert world.genau.top_block().video == "alpha one"

    def test_the_title_says_f_mode_while_it_is_on(self, tmp_path):
        world = World(tmp_path)
        mark_favorite(world.flat_dir / "gamma three.mp4", world.metadata_root, True)

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
        assert world.messages.said == [("Including no other pips", MessageLevel.WARNING)]

    def test_it_opens_in_f_mode_when_the_room_kept_it_on(self, tmp_path):
        world = World(tmp_path, WorldSetup(favorites=("beta two.mp4",), f_mode=True))

        assert world.genau.top_block() == ModeHud(video="beta two", scripted_filter=True)

    def test_opening_in_f_mode_with_no_favorites_holds_the_first_flick_and_says_so(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr("fun_time.content.genau_flick_noun", lambda: Noun(one="pip", many="pips"))

        world = World(tmp_path, WorldSetup(f_mode=True))
        world.tick()

        assert world.genau.top_block().video == "alpha one"
        assert world.status()["locked"] == "1"
        assert world.messages.said == [("Including no other pips", MessageLevel.WARNING)]


class TestItsFrame:
    def test_the_frame_the_motion_chose_is_the_frame_counted_up(self, tmp_path):
        world = World(tmp_path)
        world.send("RESUME")
        shown = set()
        for _ in range(40):
            world.tick(0.05)
            flick_frame = world.genau.own_frame()
            if flick_frame.frame is not None:
                shown.add(int(flick_frame.frame[0, 0, 0]))
                assert flick_frame.count == 8
                assert 0 <= flick_frame.played < 8

        assert len(shown) > 1

    def test_a_press_on_its_dial_moves_the_device(self, tmp_path):
        world = World(tmp_path)
        world.send("RESUME")
        world.tick(0.05)
        sent_before = len(world.sink.sent)

        world.genau.own_frame().seek_loop(0.5)
        world.tick(0.05)

        assert len(world.sink.sent) > sent_before

    def test_the_frame_carries_the_flicks_time_on_screen(self, tmp_path):
        world = World(tmp_path)
        world.tick(0.05)

        flick_frame = world.genau.own_frame()

        assert (flick_frame.elapsed_ms, flick_frame.interval_ms) == (0.0, 10_000.0)

    def test_a_press_along_its_track_puts_the_flick_that_far_into_its_time(self, tmp_path):
        world = World(tmp_path)
        world.tick(0.05)

        world.genau.own_frame().seek_time(7_000.0)

        assert world.genau.own_frame().elapsed_ms == 7_000.0

    def test_what_is_still_decoding_is_said_on_the_frame(self, tmp_path):
        world = World(tmp_path)

        assert world.genau.own_frame().loading is None


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


def test_it_answers_the_apps_volume_though_it_draws_no_volume_control_since_an_unanswered_verb_is_logged_each_time_the_volume_moves(
        tmp_path, caplog):
    world = World(tmp_path)

    with caplog.at_level(logging.WARNING):
        world.send("SET_VOLUME 70 1")

    assert [record.getMessage() for record in caplog.records
            if record.levelno >= logging.WARNING] == []


class TestTurnedAsAsked:
    _TURNED = (("alpha one.mp4", "portrait"), ("beta two.mp4", "landscape"))

    def _world(self, tmp_path, setup: WorldSetup | None = None) -> World:
        return World(tmp_path, replace(setup or WorldSetup(), metadata_root=tmp_path / "metadata", turned=self._TURNED))

    def _shown(self, world: World, steps: int = 3) -> list[str]:
        shown = []
        for _step in range(steps):
            shown.append(world.genau.top_block().video)
            world.send("NEXT")
        return shown

    def test_portrait_only_steps_through_the_portrait_flicks_alone(self, tmp_path):
        world = self._world(tmp_path)

        assert world.genau.apply_command(genau_orientations((True, False))) is True

        assert set(self._shown(world)) == {"alpha one"}

    def test_landscape_only_keeps_the_unmeasured_flicks_too(self, tmp_path):
        world = self._world(tmp_path)

        world.genau.apply_command(genau_orientations((False, True)))

        assert set(self._shown(world, steps=4)) == {"beta two", "gamma three"}

    def test_it_says_which_orientation_it_kept(self, tmp_path):
        world = self._world(tmp_path)

        world.genau.apply_command(genau_orientations((True, False)))

        assert world.messages.said[-1] == ("Portrait only", MessageLevel.NORMAL)

    def test_it_opens_turned_the_way_the_room_left_it(self, tmp_path):
        world = self._world(tmp_path, WorldSetup(orientations=(True, False)))

        assert set(self._shown(world)) == {"alpha one"}

    def test_the_favorites_it_keeps_are_the_ones_turned_as_asked(self, tmp_path):
        world = self._world(tmp_path, WorldSetup(favorites=("alpha one.mp4", "beta two.mp4"), f_mode=True))

        world.genau.apply_command(genau_orientations((False, True)))

        assert set(self._shown(world)) == {"beta two"}
