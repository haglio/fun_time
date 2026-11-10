from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from app_support.flick_folder import trash_folder_in
from funestra.funestra import User
from funestra.main_hud import ModeHud
from funestra.messages import MessageWriter

from fun_time.arrivals import LOOK_EVERY_S
from fun_time.content import no_other_flicks
from fun_time.genau_config import GenauSettings
from fun_time.genau_engine import UDP_READER_THREAD
from fun_time.media_metadata import mark_favorite
from fun_time_vr.genau_in_the_headset import GenauInTheHeadset
from main_funestra.genau import GenauChannels, GenauFlicks, GenauOutputs, GenauSeams
from main_funestra.genau_verbs import genau_f_mode
from tests.genau_fakes import (
    FakeNotifier,
    FakeSink,
    every_thread_now_but_the_port_binding_udp_reader,
)


def _frames(count: int = 8, width: int = 16, height: int = 8) -> list[np.ndarray]:
    return [np.full((height, width, 3), i, dtype=np.uint8) for i in range(count)]


class HeldDecodes:
    def __init__(self) -> None:
        self.holding = False
        self._held = []

    def __call__(self, *, target, args=(), name=""):
        if name == UDP_READER_THREAD:
            return
        if self.holding:
            self._held.append((target, args))
            return
        target(*args)

    def release(self) -> None:
        self.holding = False
        for target, args in self._held:
            target(*args)
        self._held.clear()


@dataclass(frozen=True)
class WorldSetup:
    flicks: tuple = ("alpha_180.mp4", "beta_180.mp4", "gamma.mp4")
    flat_flicks: tuple = ()
    start_flick: Path | None = None
    latest: bool = False
    start_thread: Callable = every_thread_now_but_the_port_binding_udp_reader


class World:
    def __init__(self, tmp_path: Path, setup: WorldSetup | None = None) -> None:
        setup = setup or WorldSetup()
        flicks, flat_flicks = setup.flicks, setup.flat_flicks
        self.flicks_folder = tmp_path / "flicks"
        self.vr_dir = self.flicks_folder / "VR"
        self.vr_dir.mkdir(parents=True)
        for arrived, name in enumerate(flicks, start=1):
            flick = self.vr_dir / name
            flick.write_bytes(b"flick")
            os.utime(flick, (1_000_000_000 + arrived, 1_000_000_000 + arrived))
        self.flat_dir = self.flicks_folder / "2D" / "AI"
        self.flat_dir.mkdir(parents=True)
        for name in flat_flicks:
            (self.flat_dir / name).write_bytes(b"flick")
        self.metadata_root = tmp_path / "metadata"
        state = tmp_path / "state"
        state.mkdir()
        self.channels = GenauChannels(
            command=state / "genau_cmd.txt", paused=state / "genau_paused.txt",
            status=state / "genau_status.txt", drive=state / "genau_drive.txt")
        self.sink = FakeSink()
        self.notifier = FakeNotifier()
        self.stop = threading.Event()
        self.now = 100.0
        self.notice_file = state / "notice.txt"
        self.genau = GenauInTheHeadset(
            GenauFlicks(
                folder=self.flicks_folder,
                start_flick=setup.start_flick,
                latest=setup.latest,
                metadata_root=self.metadata_root,
            ),
            settings=GenauSettings(shuffle_on_load=False),
            channels=self.channels,
            outputs=GenauOutputs(notifier=self.notifier, tcode_sink=self.sink,
                                 messages=MessageWriter(self.notice_file)),
            seams=GenauSeams(
                stop_event=self.stop,
                decode=lambda _path: _frames(),
                start_thread=setup.start_thread,
                clock=lambda: self.now,
                log=logging.getLogger("test.genau_in_the_headset"),
            ),
        )
        self.genau.tick()

    def send(self, line: str) -> None:
        self.channels.command.write_text(line + "\n", encoding="utf-8")
        self.genau.tick()
        self.genau.tick()

    def tick(self, seconds: float) -> None:
        self.now += seconds
        self.genau.tick()

    def on_screen(self) -> Path | None:
        return self.genau.own_frame().flick

    def flash_message(self) -> str:
        if not self.notice_file.exists():
            return ""
        lines = self.notice_file.read_text(encoding="utf-8").splitlines()
        return next(line.partition("=")[2] for line in lines if line.startswith("message="))


def test_genau_in_the_headset_is_a_thing_a_funestra_runs(tmp_path):
    assert isinstance(World(tmp_path).genau, User)


class TestTheFlickOnScreen:
    def test_it_opens_on_the_first_vr_flick_of_the_folder(self, tmp_path):
        world = World(tmp_path)

        assert world.on_screen() == world.vr_dir / "alpha_180.mp4"
        assert world.notifier.flicks == [world.vr_dir / "alpha_180.mp4"]

    def test_it_opens_on_the_flick_an_orchestrator_names_in_the_order_it_names(self, tmp_path):
        world = World(tmp_path, WorldSetup(latest=True, start_flick=tmp_path / "flicks" / "VR" / "beta_180.mp4"))

        assert world.on_screen() == world.vr_dir / "beta_180.mp4"
        world.send("NEXT")
        assert world.on_screen() == world.vr_dir / "alpha_180.mp4"

    def test_the_frame_handed_over_says_which_flick_it_is_of(self, tmp_path):
        world = World(tmp_path)

        flick_frame = world.genau.own_frame()

        assert flick_frame.frame is not None
        assert flick_frame.flick == world.vr_dir / "alpha_180.mp4"


class TestTheDesktopsFlicksAreBrowsedToo:
    def _both(self, tmp_path):
        return World(tmp_path, WorldSetup(flicks=("alpha_180.mp4",), flat_flicks=("scene one.mp4",)))

    def test_the_desktops_flicks_follow_the_vr_ones(self, tmp_path):
        world = self._both(tmp_path)

        world.send("NEXT")

        assert world.on_screen() == world.flat_dir / "scene one.mp4"

    def test_trash_moves_a_2d_flick_to_its_own_place_in_the_pile(self, tmp_path):
        world = self._both(tmp_path)
        world.send("NEXT")

        world.send("TRASH")

        assert (trash_folder_in(tmp_path) / "2D" / "AI" / "scene one.mp4").is_file()
        assert not (trash_folder_in(tmp_path) / "scene one.mp4").exists()


class TestNarrowingToAShape:
    def test_a_vr_flick_on_screen_gives_way_to_the_flat_ones(self, tmp_path):
        world = World(tmp_path, WorldSetup(flat_flicks=("delta.mp4", "epsilon.mp4")))

        world.send("SHAPES flat")

        assert world.on_screen() == world.flat_dir / "delta.mp4"
        world.send("NEXT")
        assert world.on_screen() == world.flat_dir / "epsilon.mp4"
        world.send("NEXT")
        assert world.on_screen() == world.flat_dir / "delta.mp4"

    def test_it_says_which_shape_it_kept(self, tmp_path):
        world = World(tmp_path, WorldSetup(flat_flicks=("delta.mp4",)))

        world.send("SHAPES flat")

        assert world.flash_message() == "2D only"

    def test_a_shape_with_no_flicks_says_only_that_there_are_none(self, tmp_path):
        world = World(tmp_path)

        world.send("SHAPES flat")

        assert world.flash_message() == no_other_flicks()

    def test_a_flick_on_screen_of_the_shape_kept_stays_up(self, tmp_path):
        world = World(tmp_path, WorldSetup(flat_flicks=("delta.mp4",), start_flick=tmp_path / "flicks" / "VR" / "beta_180.mp4"))
        up_before = list(world.notifier.flicks)

        world.send("SHAPES vr")

        assert world.on_screen() == world.vr_dir / "beta_180.mp4"
        assert world.notifier.flicks == up_before
        world.send("NEXT")
        world.send("NEXT")
        assert world.on_screen() == world.vr_dir / "alpha_180.mp4"

    def test_a_flick_decoding_ahead_when_its_shape_is_kept_goes_up_once_decoded(self, tmp_path):
        decodes = HeldDecodes()
        world = World(tmp_path, WorldSetup(flicks=("alpha_180.mp4",), flat_flicks=("delta.mp4",), start_thread=decodes))
        decodes.holding = True
        world.genau.tick()

        world.send("SHAPES flat")
        decodes.release()
        world.genau.tick()

        assert world.on_screen() == world.flat_dir / "delta.mp4"
        assert world.genau.own_frame().frame is not None

    def test_a_reorder_rescans_under_the_shape_kept(self, tmp_path):
        world = World(tmp_path, WorldSetup(flat_flicks=("delta.mp4",)))
        world.send("SHAPES flat")

        world.send("LATEST")
        world.send("NEXT")

        assert world.on_screen() == world.flat_dir / "delta.mp4"


class TestWhatArrivesInItsFolders:
    def test_under_latest_one_of_the_shape_kept_comes_up_next_and_the_other_shape_does_not(
            self, tmp_path):
        world = World(tmp_path, WorldSetup(latest=True))
        world.genau.set_showing(True)
        world.send("SHAPES vr")

        (world.vr_dir / "delta_180.mp4").write_bytes(b"flick")
        (world.flat_dir / "scene one.mp4").write_bytes(b"flick")
        world.tick(LOOK_EVERY_S)
        world.send("NEXT")
        first = world.on_screen()
        world.send("NEXT")

        assert (first, world.on_screen()) == (world.vr_dir / "delta_180.mp4",
                                              world.vr_dir / "beta_180.mp4")


class TestFMode:
    def test_it_keeps_the_favorites_of_the_shape_kept(self, tmp_path):
        world = World(tmp_path, WorldSetup(flat_flicks=("delta.mp4", "epsilon.mp4")))
        mark_favorite(world.flat_dir / "epsilon.mp4", world.metadata_root, True)
        mark_favorite(world.vr_dir / "beta_180.mp4", world.metadata_root, True)
        world.send("SHAPES flat")

        world.genau.apply_command(genau_f_mode(True))
        world.genau.tick()
        assert world.on_screen() == world.flat_dir / "epsilon.mp4"
        world.send("NEXT")

        assert world.on_screen() == world.flat_dir / "epsilon.mp4"

    def test_the_title_says_f_mode_while_it_is_on(self, tmp_path):
        world = World(tmp_path)
        mark_favorite(world.vr_dir / "alpha_180.mp4", world.metadata_root, True)

        world.genau.apply_command(genau_f_mode(True))

        assert world.genau.top_block() == ModeHud(video="alpha_180", scripted_filter=True)


class TestWhatTheHudCallsTheFlick:
    def test_the_flick_on_screen_by_its_name(self, tmp_path):
        assert World(tmp_path).genau.top_block() == ModeHud(video="alpha_180")

    def test_the_one_still_decoding_while_the_last_stays_up(self, tmp_path):
        decodes = HeldDecodes()
        world = World(tmp_path, WorldSetup(start_thread=decodes))
        decodes.holding = True
        world.send("NEXT")

        world.send("NEXT")

        assert world.on_screen() == world.vr_dir / "beta_180.mp4"
        assert world.genau.top_block() == ModeHud(video="Loading gamma.mp4")


class TestAFolderWithNothingToShow:
    def test_is_refused_at_once(self, tmp_path):
        with pytest.raises(RuntimeError, match="No video flicks"):
            World(tmp_path, WorldSetup(flicks=()))
