"""Genau as it runs on the Main Funestra: its own channel, its own picture, its
own status, over a fabricated clips folder whose decode hands back numbered frames."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
from player_core.console_hud import ModeHud

from fun_time.genau_config import GenauSettings
from main_player.genau import Genau, GenauChannels


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


class World:
    def __init__(self, tmp_path: Path, *, clips=("alpha one.mp4", "beta two.mp4", "gamma three.mp4"),
                 start_clip=None, latest=False, metadata_root=None) -> None:
        self.clips_folder = tmp_path / "clips"
        self.flat_dir = self.clips_folder / "2D" / "non_AI"
        self.flat_dir.mkdir(parents=True)
        for arrived, name in enumerate(clips, start=1):
            clip = self.flat_dir / name
            clip.write_bytes(b"clip")
            os.utime(clip, (1_000_000_000 + arrived, 1_000_000_000 + arrived))
        state = tmp_path / "state"
        state.mkdir()
        self.channels = GenauChannels(
            command=state / "genau_cmd.txt", paused=state / "genau_paused.txt",
            status=state / "genau_status.txt", drive=state / "genau_drive.txt")
        self.sink = FakeSink()
        self.notifier = FakeNotifier()
        self.clock = Clock()
        self.genau = Genau(
            clips_folder=self.clips_folder,
            settings=GenauSettings(shuffle_on_load=False),
            channels=self.channels,
            notifier=self.notifier,
            tcode_sink=self.sink,
            start_clip=start_clip,
            latest=latest,
            metadata_root=metadata_root,
            decode=lambda _path: _frames(),
            start_thread=_run_now,
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


class TestTheClipOnScreen:
    def test_it_opens_on_the_first_clip_of_the_folder_and_tells_the_audio_companion(self, tmp_path):
        world = World(tmp_path)

        assert world.genau.top_block() == ModeHud(video="alpha one")
        assert world.notifier.clips == [world.flat_dir / "alpha one.mp4"]

    def test_its_verbs_arrive_on_its_own_channel_and_never_on_the_funestras(self, tmp_path):
        world = World(tmp_path)

        assert world.genau.apply_command("NEXT") is False
        world.send("NEXT")

        assert world.genau.top_block() == ModeHud(video="beta two")

    def test_it_publishes_its_own_status_and_adds_no_line_to_the_funestras(self, tmp_path):
        world = World(tmp_path)

        world.tick()

        assert world.status()["clip"] == str(world.flat_dir / "alpha one.mp4")
        assert world.genau.status_fields() == {}

    def test_weird_moves_the_clip_to_the_pile_beside_the_folder(self, tmp_path):
        world = World(tmp_path)

        world.send("WEIRD")

        assert (tmp_path / "weird" / "2D" / "non_AI" / "alpha one.mp4").is_file()
        assert world.genau.top_block() == ModeHud(video="beta two")

    def test_latest_rescans_the_folder_newest_first(self, tmp_path):
        world = World(tmp_path)
        newest = world.flat_dir / "delta four.mp4"
        newest.write_bytes(b"clip")
        os.utime(newest, (4_000_000_000, 4_000_000_000))

        world.send("LATEST")

        assert world.genau.top_block() == ModeHud(video="delta four")


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

    def test_a_seek_along_its_picture_moves_the_device(self, tmp_path):
        world = World(tmp_path)
        world.send("RESUME")
        world.tick(0.05)
        sent_before = len(world.sink.sent)

        world.genau.picture().seek(0.5)
        world.tick(0.05)

        assert len(world.sink.sent) > sent_before

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
