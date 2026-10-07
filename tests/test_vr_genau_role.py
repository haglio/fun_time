"""Genau's role in the VR player: the clip player's contract, in-process.

Driven the way the desktop drives Genau -- verbs in the command file, the
paused flag, the console file -- against a fabricated clips folder whose
"decode" hands back numbered frames, and asked what a headset needs to know:
which frame to show, in which projection, whether to show it at all, and what
the panel should draw.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from player_core.console import ConsoleModel, console_text
from player_core.modes import MainMode

from fun_time.console_buttons import MainSlot, console_rows, osr2_controls
from fun_time_vr.genau_role import GenauRole, run_ticks
from fun_time_vr.genau_settings import GenauSettings
from fun_time_vr.projection import EQUIRECT_180_SBS, FISHEYE_190_SBS, FLAT


class FakeSink:
    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    def send(self, command: str) -> None:
        self.sent.append(command)

    def close(self) -> None:
        self.closed = True


class FakeNotifier:
    def __init__(self):
        self.clips: list[Path] = []
        self.visible: list[bool] = []

    def notify_clip(self, path: Path) -> None:
        self.clips.append(path)

    def notify_visible(self, is_visible: bool) -> None:
        self.visible.append(is_visible)


class Clock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _frames(count: int = 8, width: int = 16, height: int = 8) -> list[np.ndarray]:
    """*count* frames, each a flat picture of its own index."""
    return [np.full((height, width, 3), i, dtype=np.uint8) for i in range(count)]


def _run_now(*, target, args=(), name=""):
    """A thread starter that runs the decode on the spot -- the loader's thread
    is then no longer a thread, and every frame is in the cache by the next
    tick.  The UDP reader is the one job it must not run: that would bind a port
    and loop; it is simply not started."""
    if name == "genau-udp":
        return
    target(*args)
    return


class Genau:
    """A role wired the way the VR player wires it, over a fabricated clips
    folder: its VR clips, and its 2D ones a folder deeper."""

    def __init__(self, tmp_path: Path, *, clips=("alpha_180.mp4", "beta_180.mp4", "gamma.mp4"),
                 flat_clips=(), decode=None, start_clip=None, latest=False, settings=None,
                 console_file=None, start_thread=_run_now, metadata_root=None):
        self.clips_folder = tmp_path / "clips"
        self.clips_dir = self.clips_folder / "VR"
        self.clips_dir.mkdir(parents=True)
        for arrived, name in enumerate(clips, start=1):  # first named is the oldest
            clip = self.clips_dir / name
            clip.write_bytes(b"clip")
            os.utime(clip, (1_000_000_000 + arrived, 1_000_000_000 + arrived))
        self.flat_dir = self.clips_folder / "2D" / "AI"
        self.flat_dir.mkdir(parents=True)
        for name in flat_clips:
            (self.flat_dir / name).write_bytes(b"clip")
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.command_file = self.state / "genau_cmd.txt"
        self.paused_file = self.state / "genau_paused.txt"
        self.sink = FakeSink()
        self.notifier = FakeNotifier()
        self.stop = threading.Event()
        self.clock = Clock()
        self.role = GenauRole(
            clips_folder=self.clips_folder,
            settings=settings or GenauSettings(shuffle_on_load=False),
            command_file=self.command_file,
            paused_file=self.paused_file,
            drive_file=self.state / "genau_drive.txt",
            console_file=console_file,
            notifier=self.notifier,
            tcode_sink=self.sink,
            stop_event=self.stop,
            start_clip=start_clip,
            latest=latest,
            metadata_root=metadata_root,
            decode=decode or (lambda _path: _frames()),
            start_thread=start_thread,
            clock=self.clock,
            log=logging.getLogger("test.genau_role"),
        )

    def send(self, line: str) -> None:
        """Write one line where Fun Time writes it and run two turns: the first
        drains it, and a switch it deferred to a decode lands on the second --
        the decode here is on the spot, so the frames are cached by then."""
        self.command_file.write_text(line + "\n", encoding="utf-8")
        self.role.refresh()
        self.role.refresh()

    def tick(self, seconds: float = 0.05) -> None:
        self.clock.now += seconds
        self.role.refresh()


class TestTheClipOnScreen:
    def test_it_opens_on_the_first_clip_of_the_folder(self, tmp_path):
        genau = Genau(tmp_path)

        assert genau.role.current_clip == genau.clips_dir / "alpha_180.mp4"
        assert genau.notifier.clips == [genau.clips_dir / "alpha_180.mp4"]

    def test_it_opens_on_the_clip_an_orchestrator_names(self, tmp_path):
        genau = Genau(tmp_path, start_clip=tmp_path / "clips" / "VR" / "gamma.mp4")

        assert genau.role.current_clip == genau.clips_dir / "gamma.mp4"

    def test_it_opens_in_the_order_an_orchestrator_names_and_still_on_its_clip(self, tmp_path):
        """Latest arrives with the clip, at construction: as the verb afterwards
        it would browse the new order from its top, over the clip the session
        was being resumed onto.  Newest-first here is gamma, beta, alpha."""
        genau = Genau(tmp_path, latest=True, start_clip=tmp_path / "clips" / "VR" / "beta_180.mp4")

        assert genau.role.current_clip == genau.clips_dir / "beta_180.mp4"
        genau.send("NEXT")
        assert genau.role.current_clip == genau.clips_dir / "alpha_180.mp4"

    def test_next_and_prev_walk_the_folder(self, tmp_path):
        genau = Genau(tmp_path)

        genau.send("NEXT")
        assert genau.role.current_clip == genau.clips_dir / "beta_180.mp4"
        genau.send("PREV")
        assert genau.role.current_clip == genau.clips_dir / "alpha_180.mp4"

    def test_a_clip_picked_in_the_browser_goes_up_and_the_order_goes_on_from_it(self, tmp_path):
        genau = Genau(tmp_path, flat_clips=("Delta Loop.mp4",))

        genau.send(f"PLAY_FILE {genau.flat_dir / 'Delta Loop.mp4'}")
        picked = genau.role.current_clip
        genau.send("NEXT")

        assert picked == genau.flat_dir / "Delta Loop.mp4"
        assert genau.role.current_clip == genau.clips_dir / "alpha_180.mp4"

    def test_a_flip_is_kept_in_the_clips_record_in_the_metadata_folder(self, tmp_path):
        library = tmp_path / "videos"
        (library / "genau").mkdir(parents=True)
        genau = Genau(library / "genau", metadata_root=library / "metadata")

        genau.send("FLIP_ENDS")

        record = library / "metadata" / "genau" / "clips" / "VR" / "alpha_180.json"
        assert json.loads(record.read_text(encoding="utf-8")) == {"genau": {"flipped": True}}

    def test_weird_moves_the_clip_to_its_own_place_in_the_pile_beside_the_folder(self, tmp_path):
        genau = Genau(tmp_path)

        genau.send("WEIRD")

        assert (tmp_path / "weird" / "VR" / "alpha_180.mp4").is_file()
        assert not (genau.clips_dir / "alpha_180.mp4").exists()
        assert genau.role.current_clip == genau.clips_dir / "beta_180.mp4"

    def test_a_weird_that_could_not_move_the_clip_is_logged_as_an_error(
        self, tmp_path, monkeypatch, caplog,
    ):
        """The discard that was asked for did not happen, so it reads red in the
        headset rather than a warning's yellow."""
        genau = Genau(tmp_path)

        def locked(_path, _weird_dir):
            raise OSError("the file is open elsewhere")

        monkeypatch.setattr("fun_time_vr.genau_role.move_clip_to_weird", locked)
        with caplog.at_level(logging.DEBUG, logger="test.genau_role"):
            genau.send("WEIRD")

        assert [r.levelno for r in caplog.records
                if r.name == "test.genau_role" and "Could not move" in r.getMessage()] == [
            logging.ERROR]

    def test_latest_rescans_the_folder_newest_first(self, tmp_path):
        genau = Genau(tmp_path)
        newest = genau.clips_dir / "delta_180.mp4"
        newest.write_bytes(b"clip")
        os.utime(newest, (4_000_000_000, 4_000_000_000))

        genau.send("LATEST")

        assert genau.role.current_clip == newest
        genau.send("NEXT")
        assert genau.role.current_clip != newest   # the rescan took the folder up whole


class TestNarrowingToAShape:
    """The console's VR and flat pair, said to Genau as SHAPES."""

    def test_a_vr_clip_on_screen_gives_way_to_the_flat_ones(self, tmp_path):
        genau = Genau(tmp_path, flat_clips=("delta.mp4", "epsilon.mp4"))

        genau.send("SHAPES flat")

        assert genau.role.current_clip == genau.flat_dir / "delta.mp4"
        genau.send("NEXT")
        assert genau.role.current_clip == genau.flat_dir / "epsilon.mp4"
        genau.send("NEXT")
        assert genau.role.current_clip == genau.flat_dir / "delta.mp4"

    def test_a_clip_on_screen_of_the_shape_kept_stays_up(self, tmp_path):
        genau = Genau(tmp_path, flat_clips=("delta.mp4",),
                      start_clip=tmp_path / "clips" / "VR" / "beta_180.mp4")
        up_before = list(genau.notifier.clips)

        genau.send("SHAPES vr")

        assert genau.role.current_clip == genau.clips_dir / "beta_180.mp4"
        assert genau.notifier.clips == up_before
        genau.send("NEXT")
        genau.send("NEXT")
        assert genau.role.current_clip == genau.clips_dir / "alpha_180.mp4"

    def test_a_clip_decoding_ahead_when_its_shape_is_kept_goes_up_once_decoded(self, tmp_path):
        decodes = HeldDecodes()
        genau = Genau(tmp_path, clips=("alpha_180.mp4",), flat_clips=("delta.mp4",),
                      start_thread=decodes)
        decodes.holding = True
        genau.role.refresh()
        genau.role.take_frame()

        genau.send("SHAPES flat")
        decodes.release()
        genau.role.refresh()

        assert genau.role.current_clip == genau.flat_dir / "delta.mp4"
        assert genau.role.take_frame() is not None

    def test_a_reorder_rescans_under_the_shape_kept(self, tmp_path):
        genau = Genau(tmp_path, flat_clips=("delta.mp4",))
        genau.send("SHAPES flat")

        genau.send("LATEST")
        genau.send("NEXT")

        assert genau.role.current_clip == genau.flat_dir / "delta.mp4"


class TestTheFrameHandedToTheRenderThread:
    def test_the_first_frame_is_there_once_the_clip_is_decoded(self, tmp_path):
        genau = Genau(tmp_path)

        genau.role.refresh()
        frame = genau.role.take_frame()

        assert frame is not None
        assert frame.shape == (8, 16, 3)

    def test_a_frame_is_taken_once(self, tmp_path):
        genau = Genau(tmp_path)
        genau.role.refresh()
        genau.role.take_frame()

        assert genau.role.take_frame() is None

    def test_the_motion_scrubs_the_clip(self, tmp_path):
        """The frame is the picture of where the device is: with the hand
        driving, ticks that move the motion hand over different frames."""
        genau = Genau(tmp_path)
        genau.send("RESUME")
        shown = set()
        for _ in range(40):
            genau.tick(0.05)
            frame = genau.role.take_frame()
            if frame is not None:
                shown.add(int(frame[0, 0, 0]))

        assert len(shown) > 1

    def test_a_paused_hand_holds_the_frame(self, tmp_path):
        genau = Genau(tmp_path)
        genau.send("RESUME")
        for _ in range(10):
            genau.tick(0.05)
        genau.send("PAUSE")
        genau.role.take_frame()

        for _ in range(10):
            genau.tick(0.05)

        assert genau.role.take_frame() is None


class TestWhatTheHeadsetIsToldToShow:
    def test_a_fresh_role_shows_its_clip(self, tmp_path):
        assert Genau(tmp_path).role.showing is True

    def test_hud_on_means_the_video_shows_and_the_clip_does_not(self, tmp_path):
        """Video mode, on the desktop: Genau is the see-through layer over
        The main player's video.  In the headset there is nothing to see through, so the
        clip simply steps aside."""
        genau = Genau(tmp_path)

        genau.send("HUD_ON")
        assert genau.role.showing is False
        genau.send("HUD_OFF")
        assert genau.role.showing is True

    def test_a_clip_named_for_its_projection_is_watched_in_it(self, tmp_path):
        genau = Genau(tmp_path, clips=("scene_fisheye.mp4",))
        genau.role.refresh()

        genau.role.take_frame()

        assert genau.role.projection == FISHEYE_190_SBS

    def test_a_clip_in_the_vr_folder_is_a_vr180_master_by_convention(self, tmp_path):
        genau = Genau(tmp_path, clips=("scene one.mp4",))
        genau.role.refresh()

        genau.role.take_frame()

        assert genau.role.projection == EQUIRECT_180_SBS

    def test_the_projection_follows_the_clip(self, tmp_path):
        genau = Genau(tmp_path, clips=("one_fisheye.mp4", "two.mp4"))
        genau.role.refresh()
        genau.role.take_frame()

        genau.send("NEXT")
        genau.role.take_frame()

        assert genau.role.projection == EQUIRECT_180_SBS


class HeldDecodes:
    """A thread starter that runs a decode on the spot until told to hold them,
    for a clip whose frames are still coming while another's are on screen."""

    def __init__(self):
        self.holding = False
        self._held = []

    def __call__(self, *, target, args=(), name=""):
        if name == "genau-udp":
            return
        if self.holding:
            self._held.append((target, args))
            return
        target(*args)

    def release(self):
        self.holding = False
        for target, args in self._held:
            target(*args)
        self._held.clear()


class TestTheWrapFollowsThePictureOnScreen:
    def test_a_vr_clip_keeps_its_wrap_until_the_flat_clip_after_it_is_up(self, tmp_path):
        decodes = HeldDecodes()
        genau = Genau(tmp_path, flat_clips=("delta.mp4",), start_thread=decodes)
        genau.role.refresh()
        genau.role.take_frame()

        decodes.holding = True
        genau.send("SHAPES flat")
        assert genau.role.current_clip == genau.flat_dir / "delta.mp4"
        assert genau.role.take_frame() is None
        assert genau.role.projection == EQUIRECT_180_SBS

        decodes.release()
        genau.role.refresh()
        assert genau.role.take_frame() is not None
        assert genau.role.projection == FLAT


class TestWhatItPublishes:
    def test_the_status_file_goes_beside_the_command_file(self, tmp_path):
        """Where the dispatch loop and the arbiter read it."""
        genau = Genau(tmp_path)

        genau.role.refresh()

        text = (genau.state / "genau_status.txt").read_text(encoding="utf-8")
        assert "clip=" in text
        assert "alpha_180.mp4" in text

    def test_the_drive_readout_goes_out_for_the_holds_to_read(self, tmp_path):
        genau = Genau(tmp_path)

        genau.role.refresh()

        assert (genau.state / "genau_drive.txt").is_file()

    def test_the_console_it_composes_is_kept_for_the_panel(self, tmp_path):
        genau = Genau(tmp_path)

        genau.role.refresh()

        hud = genau.role.console_hud
        assert hud is not None
        assert hud.modes.video == "alpha_180"
        assert hud.drive is not None

    def test_the_console_file_fun_time_publishes_is_read(self, tmp_path):
        published = ConsoleModel(main_mode=MainMode.GENAU,
                                 rows=console_rows(MainSlot(main_mode=MainMode.GENAU),
                                                   in_vr=True),
                                 osr2_controls=osr2_controls(broker=True))
        console = tmp_path / "main_player_console.json"
        console.write_text(console_text(published), encoding="utf-8")
        genau = Genau(tmp_path, console_file=console)

        genau.role.refresh()

        read = genau.role.console_hud.console
        assert read.main_mode is MainMode.GENAU
        assert (read.rows, read.osr2_controls) == (published.rows, published.osr2_controls)

    def test_the_published_sound_level_is_kept_for_the_chip(self, tmp_path):
        genau = Genau(tmp_path)

        genau.send("SET_VOLUME 40 1")

        assert (genau.role.volume, genau.role.muted) == (40, True)


class TestTheHandOnTheWire:
    def test_a_resumed_hand_drives_the_device(self, tmp_path):
        genau = Genau(tmp_path)

        genau.send("RESUME")
        genau.tick(0.05)

        assert genau.sink.sent
        assert genau.sink.sent[0].startswith("L0")

    def test_a_paused_hand_sends_nothing(self, tmp_path):
        genau = Genau(tmp_path)

        for _ in range(5):
            genau.tick(0.05)

        assert genau.sink.sent == []

    def test_the_hands_verbs_move_the_hand(self, tmp_path):
        genau = Genau(tmp_path)

        genau.send("SPEED_UP")
        genau.send("AMP 70")

        assert genau.role.robot_hand.speed == 55
        assert genau.role.robot_hand.amplitude == 70

    def test_closing_lets_go_of_the_device(self, tmp_path):
        genau = Genau(tmp_path)

        genau.role.close()

        assert genau.sink.closed is True


class TestTheEnginesOwnThread:
    def test_quit_ends_the_ticks(self, tmp_path):
        genau = Genau(tmp_path)
        genau.command_file.write_text("QUIT\n", encoding="utf-8")

        thread = threading.Thread(target=run_ticks, args=(genau.role, genau.stop), kwargs={"hz": 200.0})
        thread.start()
        thread.join(timeout=5.0)

        assert not thread.is_alive()
        assert genau.stop.is_set()


class TestAFolderWithNothingToShow:
    def test_is_refused_at_once(self, tmp_path):
        with pytest.raises(RuntimeError, match="No video clips"):
            Genau(tmp_path, clips=())


class TestTheDesktopsClipsAreBrowsedToo:
    """The headset browses its VR clips and the desktop's flat ones as one
    sequence, the way the main rotation joins the VR library to the desktop's."""

    def _both(self, tmp_path):
        return Genau(tmp_path, clips=("alpha_180.mp4",), flat_clips=("scene one.mp4",))

    def test_the_desktops_clips_follow_the_vr_ones(self, tmp_path):
        genau = self._both(tmp_path)

        genau.send("NEXT")

        assert genau.role.current_clip == genau.flat_dir / "scene one.mp4"

    def test_a_desktop_clip_is_watched_flat(self, tmp_path):
        genau = self._both(tmp_path)
        genau.role.refresh()
        genau.role.take_frame()
        genau.send("NEXT")

        genau.role.take_frame()

        assert genau.role.projection == FLAT

    def test_weird_moves_a_2d_clip_to_its_own_place_in_the_pile(self, tmp_path):
        genau = self._both(tmp_path)
        genau.send("NEXT")

        genau.send("WEIRD")

        assert (tmp_path / "weird" / "2D" / "AI" / "scene one.mp4").is_file()
        assert not (tmp_path / "weird" / "scene one.mp4").exists()


class TestThePlayheadItPublishes:
    """What the clip's bar draws.  It counts UP while the frame that is on screen
    counts DOWN: player_core shows a clip from its last frame back, so a cursor
    drawn straight off that index walked backwards as the motion went on."""

    def _role(self, index, count):
        role = GenauRole.__new__(GenauRole)
        role._renderer = SimpleNamespace(
            current_frame_index=index,
            current_clip_entry=lambda: None if count is None else {"frames": [0] * count},
        )
        return role

    def test_it_counts_up_across_the_clip(self):
        assert self._role(19, 20).playhead == (0, 20)
        assert self._role(12, 20).playhead == (7, 20)
        assert self._role(0, 20).playhead == (19, 20)

    def test_nothing_to_draw_before_a_clip_is_up(self):
        assert self._role(None, None).playhead == (0, 0)


class TestSeekingFromTheBar:
    """A squeeze on the clip's bar reaches player_core's own seek, which puts the
    device where the press asked -- the frame being a picture of where it is."""

    def test_it_hands_the_fraction_straight_to_the_engine(self):
        role = GenauRole.__new__(GenauRole)
        asked: list[float] = []
        role._controller = SimpleNamespace(seek_the_clip=asked.append)

        role.seek(0.25)
        role.seek(0.9)

        assert asked == [0.25, 0.9]
