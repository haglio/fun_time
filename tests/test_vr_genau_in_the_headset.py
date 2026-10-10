"""Genau as it runs on the headset's Main Funestra: the desktop's own, browsing
its VR clips with the desktop's flat ones, narrowed to a shape when the room
asks, and handing each frame over with the clip it is a frame of."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np
import pytest
from player_core.console_hud import ModeHud
from player_core.funestra import User

from fun_time.genau_config import GenauSettings
from fun_time_vr.genau_in_the_headset import GenauInTheHeadset
from main_player.genau import GenauChannels


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
        self.clips: list[Path] = []
        self.visible: list[bool] = []
        self.closed = False

    def notify_clip(self, path: Path) -> None:
        self.clips.append(path)

    def notify_visible(self, is_visible: bool) -> None:
        self.visible.append(is_visible)

    def close(self) -> None:
        self.closed = True


def _frames(count: int = 8, width: int = 16, height: int = 8) -> list[np.ndarray]:
    return [np.full((height, width, 3), i, dtype=np.uint8) for i in range(count)]


def _run_now(*, target, args=(), name=""):
    """The loader's thread runs on the spot; the UDP reader, which would bind a
    port and loop, is not started at all."""
    if name == "genau-udp":
        return
    target(*args)


class HeldDecodes:
    """A thread starter that runs a decode on the spot until told to hold them,
    for a clip whose frames are still coming while another's are on screen."""

    def __init__(self) -> None:
        self.holding = False
        self._held = []

    def __call__(self, *, target, args=(), name=""):
        if name == "genau-udp":
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


class World:
    """Genau wired the way the headset wires it, over a fabricated clips
    folder: its VR clips, and its flat ones a folder deeper."""

    def __init__(self, tmp_path: Path, *, clips=("alpha_180.mp4", "beta_180.mp4", "gamma.mp4"),
                 flat_clips=(), start_clip=None, latest=False, start_thread=_run_now) -> None:
        self.clips_folder = tmp_path / "clips"
        self.vr_dir = self.clips_folder / "VR"
        self.vr_dir.mkdir(parents=True)
        for arrived, name in enumerate(clips, start=1):
            clip = self.vr_dir / name
            clip.write_bytes(b"clip")
            os.utime(clip, (1_000_000_000 + arrived, 1_000_000_000 + arrived))
        self.flat_dir = self.clips_folder / "2D" / "AI"
        self.flat_dir.mkdir(parents=True)
        for name in flat_clips:
            (self.flat_dir / name).write_bytes(b"clip")
        state = tmp_path / "state"
        state.mkdir()
        self.channels = GenauChannels(
            command=state / "genau_cmd.txt", paused=state / "genau_paused.txt",
            status=state / "genau_status.txt", drive=state / "genau_drive.txt")
        self.sink = FakeSink()
        self.notifier = FakeNotifier()
        self.stop = threading.Event()
        self.genau = GenauInTheHeadset(
            clips_folder=self.clips_folder,
            settings=GenauSettings(shuffle_on_load=False),
            channels=self.channels,
            notifier=self.notifier,
            tcode_sink=self.sink,
            stop_event=self.stop,
            start_clip=start_clip,
            latest=latest,
            decode=lambda _path: _frames(),
            start_thread=start_thread,
            log=logging.getLogger("test.genau_in_the_headset"),
        )
        self.genau.tick()  # the first pass puts the first clip's frame up

    def send(self, line: str) -> None:
        self.channels.command.write_text(line + "\n", encoding="utf-8")
        self.genau.tick()
        self.genau.tick()

    def on_screen(self) -> Path | None:
        return self.genau.picture().clip


def test_genau_in_the_headset_is_a_thing_a_funestra_runs(tmp_path):
    assert isinstance(World(tmp_path).genau, User)


class TestTheClipOnScreen:
    def test_it_opens_on_the_first_vr_clip_of_the_folder(self, tmp_path):
        world = World(tmp_path)

        assert world.on_screen() == world.vr_dir / "alpha_180.mp4"
        assert world.notifier.clips == [world.vr_dir / "alpha_180.mp4"]

    def test_it_opens_on_the_clip_an_orchestrator_names_in_the_order_it_names(self, tmp_path):
        world = World(tmp_path, latest=True, start_clip=tmp_path / "clips" / "VR" / "beta_180.mp4")

        assert world.on_screen() == world.vr_dir / "beta_180.mp4"
        world.send("NEXT")
        assert world.on_screen() == world.vr_dir / "alpha_180.mp4"

    def test_the_frame_handed_over_says_which_clip_it_is_of(self, tmp_path):
        world = World(tmp_path)

        picture = world.genau.picture()

        assert picture.frame is not None
        assert picture.clip == world.vr_dir / "alpha_180.mp4"


class TestTheDesktopsClipsAreBrowsedToo:
    """The headset browses its VR clips and the desktop's flat ones as one
    sequence, the way the main rotation joins the VR library to the desktop's."""

    def _both(self, tmp_path):
        return World(tmp_path, clips=("alpha_180.mp4",), flat_clips=("scene one.mp4",))

    def test_the_desktops_clips_follow_the_vr_ones(self, tmp_path):
        world = self._both(tmp_path)

        world.send("NEXT")

        assert world.on_screen() == world.flat_dir / "scene one.mp4"

    def test_weird_moves_a_2d_clip_to_its_own_place_in_the_pile(self, tmp_path):
        world = self._both(tmp_path)
        world.send("NEXT")

        world.send("WEIRD")

        assert (tmp_path / "weird" / "2D" / "AI" / "scene one.mp4").is_file()
        assert not (tmp_path / "weird" / "scene one.mp4").exists()


class TestNarrowingToAShape:
    """The console's VR and flat pair, said to Genau as SHAPES."""

    def test_a_vr_clip_on_screen_gives_way_to_the_flat_ones(self, tmp_path):
        world = World(tmp_path, flat_clips=("delta.mp4", "epsilon.mp4"))

        world.send("SHAPES flat")

        assert world.on_screen() == world.flat_dir / "delta.mp4"
        world.send("NEXT")
        assert world.on_screen() == world.flat_dir / "epsilon.mp4"
        world.send("NEXT")
        assert world.on_screen() == world.flat_dir / "delta.mp4"

    def test_a_clip_on_screen_of_the_shape_kept_stays_up(self, tmp_path):
        world = World(tmp_path, flat_clips=("delta.mp4",),
                      start_clip=tmp_path / "clips" / "VR" / "beta_180.mp4")
        up_before = list(world.notifier.clips)

        world.send("SHAPES vr")

        assert world.on_screen() == world.vr_dir / "beta_180.mp4"
        assert world.notifier.clips == up_before
        world.send("NEXT")
        world.send("NEXT")
        assert world.on_screen() == world.vr_dir / "alpha_180.mp4"

    def test_a_clip_decoding_ahead_when_its_shape_is_kept_goes_up_once_decoded(self, tmp_path):
        decodes = HeldDecodes()
        world = World(tmp_path, clips=("alpha_180.mp4",), flat_clips=("delta.mp4",),
                      start_thread=decodes)
        decodes.holding = True
        world.genau.tick()

        world.send("SHAPES flat")
        decodes.release()
        world.genau.tick()

        assert world.on_screen() == world.flat_dir / "delta.mp4"
        assert world.genau.picture().frame is not None

    def test_a_reorder_rescans_under_the_shape_kept(self, tmp_path):
        world = World(tmp_path, flat_clips=("delta.mp4",))
        world.send("SHAPES flat")

        world.send("LATEST")
        world.send("NEXT")

        assert world.on_screen() == world.flat_dir / "delta.mp4"


class TestWhatThePanelCallsTheClip:
    def test_the_clip_on_screen_by_its_name(self, tmp_path):
        assert World(tmp_path).genau.top_block() == ModeHud(video="alpha_180")

    def test_the_one_still_decoding_while_the_last_stays_up(self, tmp_path):
        """The headset's panel names what is on its way rather than what is
        still up, as it always has there.  The clip after the first is decoded
        ahead by the first pass; the one after that is not, so a step onto it
        waits for its decode."""
        decodes = HeldDecodes()
        world = World(tmp_path, start_thread=decodes)
        decodes.holding = True
        world.send("NEXT")

        world.send("NEXT")

        assert world.on_screen() == world.vr_dir / "beta_180.mp4"
        assert world.genau.top_block() == ModeHud(video="Loading gamma.mp4")


class TestAFolderWithNothingToShow:
    def test_is_refused_at_once(self, tmp_path):
        with pytest.raises(RuntimeError, match="No video clips"):
            World(tmp_path, clips=())
