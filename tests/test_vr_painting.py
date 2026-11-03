from __future__ import annotations

import logging
from types import SimpleNamespace

from fun_time_vr.frame_relay import SLOTS, FrameHandover, FrameRelay, StillAsked
from fun_time_vr.headset_engine import HeadsetEngine
from fun_time_vr.paint_watch import FLUSH_AFTER_S, GIVE_UP_AFTER_S, NO_FRAME_AFTER_S
from fun_time_vr.painting import Painting, Showing
from tests.paint_fakes import AGpu, AnEngine, ATarget


class _AClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _frames(relay=None, still=None) -> FrameHandover:
    return FrameHandover("main-video", 1024, relay or FrameRelay(), still or StillAsked())


def _a_painting(engine=None, *, gpu=None, frames=None, perf=None, clock=None):
    return Painting(engine or AnEngine(), frames or _frames(), gpu=gpu or AGpu(),
                    perf=perf, clock=clock or _AClock())


def _targets():
    return [ATarget(n) for n in range(SLOTS)]


class TestPaintingAFrame:
    def test_a_frame_is_drawn_into_a_free_slot_at_the_capped_size(self):
        engine, gpu = AnEngine(dims=(3840, 2160)), AGpu()
        painting = _a_painting(engine, gpu=gpu)
        targets = _targets()

        idle = painting.paint(targets, None)

        assert idle is False
        assert engine.rendered == [(10, 1024, 576, True)]
        assert (targets[0].width, targets[0].height) == (1024, 576)
        assert gpu.flushes == 1

    def test_a_drawn_frame_is_relayed_once_the_gpu_has_reached_its_mark(self):
        relay = FrameRelay()
        painting = _a_painting(AnEngine(video="b.mp4"), frames=_frames(relay=relay))
        targets = _targets()

        painting.paint(targets, None)
        assert relay.take() is None
        painting.paint(targets, None)

        frame = relay.take()
        assert (frame.slot, frame.texture, frame.width, frame.height, frame.video) == (
            0, 20, 1024, 576, "b.mp4")

    def test_a_frame_the_gpu_has_not_finished_waits_and_is_not_relayed(self):
        gpu, relay = AGpu(reaches_marks=False), FrameRelay()
        painting = _a_painting(gpu=gpu, frames=_frames(relay=relay))
        targets = _targets()

        painting.paint(targets, None)
        idle = painting.paint(targets, None)

        assert idle is True
        assert relay.take() is None

    def test_a_still_asked_for_is_left_after_the_next_painted_frame(self):
        engine, still = AnEngine(), StillAsked()
        painting = _a_painting(engine, frames=_frames(still=still))
        targets, small = _targets(), ATarget(9)
        small.ensure(512, 256)

        still.ask()
        painting.paint(targets, small)
        painting.paint(targets, small)

        assert still.take() == ("pixels", 512, 256)
        assert (19, 512, 256, False) in engine.rendered

    def test_an_engine_with_nothing_to_draw_lets_the_thread_rest(self):
        engine = AnEngine()
        engine.has_frame_to_draw = False

        assert _a_painting(engine).paint(_targets(), None) is True
        assert engine.rendered == []

    def test_a_frame_with_no_size_yet_is_not_drawn(self):
        engine = AnEngine(dims=(0, 0))

        assert _a_painting(engine).paint(_targets(), None) is True
        assert engine.rendered == []

    def test_a_frame_that_mpv_says_is_not_one_to_show_is_not_relayed(self):
        engine, relay = AnEngine(video=None), FrameRelay()
        painting = _a_painting(engine, frames=_frames(relay=relay))
        targets = _targets()

        painting.paint(targets, None)
        painting.paint(targets, None)

        assert engine.rendered
        assert relay.take() is None

    def test_each_paint_is_timed_for_the_perf_log(self):
        noted = []
        painting = _a_painting(perf=SimpleNamespace(note=lambda stage, ms: noted.append(stage)))

        painting.paint(_targets(), None)

        assert noted == ["paint"]


class TestAGpuThatIsSlow:
    def test_a_frame_waited_on_for_a_second_is_flushed_and_said_once(self, caplog):
        clock, gpu = _AClock(), AGpu(reaches_marks=False)
        painting = _a_painting(gpu=gpu, clock=clock)
        targets = _targets()
        painting.paint(targets, None)

        with caplog.at_level(logging.WARNING, logger="fun_time_vr.painting"):
            painting.paint(targets, None)
            clock.t = FLUSH_AFTER_S
            painting.paint(targets, None)
            painting.paint(targets, None)

        assert gpu.flushes == 2
        assert caplog.messages == ["The main-video video's frame has waited 1s for the GPU; flushing"]

    def test_a_frame_the_gpu_never_finishes_is_shown_anyway(self, caplog):
        clock, relay = _AClock(), FrameRelay()
        painting = _a_painting(gpu=AGpu(reaches_marks=False), frames=_frames(relay=relay), clock=clock)
        targets = _targets()
        painting.paint(targets, None)
        painting.paint(targets, None)

        clock.t = GIVE_UP_AFTER_S
        with caplog.at_level(logging.ERROR, logger="fun_time_vr.painting"):
            painting.paint(targets, None)

        assert relay.take() is not None
        assert caplog.messages == [
            "The main-video video's frame never finished on the GPU in 5s; showing it anyway"]


class TestAVideoThatStopsDrawing:
    def test_one_that_plays_on_without_a_frame_for_six_seconds_is_said_once(self, caplog):
        clock, engine = _AClock(), AnEngine()
        engine.has_frame_to_draw = False
        painting = _a_painting(engine, clock=clock)

        with caplog.at_level(logging.WARNING, logger="fun_time_vr.painting"):
            for second in range(int(NO_FRAME_AFTER_S) + 3):
                clock.t = float(second)
                engine.position_ms = second * 1000.0
                painting.paint(_targets(), None)

        assert caplog.messages == [
            "The main-video video has drawn nothing for 6s while it played on"]

    def test_one_that_is_paused_is_not_blamed(self, caplog):
        clock, engine = _AClock(), AnEngine()
        engine.has_frame_to_draw = False
        painting = _a_painting(engine, clock=clock)

        with caplog.at_level(logging.WARNING, logger="fun_time_vr.painting"):
            for second in range(int(NO_FRAME_AFTER_S) + 3):
                clock.t = float(second)
                painting.paint(_targets(), None)

        assert caplog.messages == []


class TestShowingTheNewestFrame:
    def test_nothing_relayed_shows_nothing(self):
        assert Showing(_frames(), gpu=AGpu()).show_newest(ATarget(5)) is False

    def test_a_relayed_frame_is_copied_into_the_screens_texture_at_its_own_size(self):
        gpu, relay = AGpu(), FrameRelay()
        relay.painted(1, texture=21, width=640, height=360, video="c.mp4")
        target = ATarget(5)

        shown = Showing(_frames(relay), gpu=gpu).show_newest(target)

        assert shown is True
        assert gpu.copied == [(21, 25)]
        assert (target.width, target.height, target.video, target.painted) == (640, 360, "c.mp4", True)

    def test_a_target_is_marked_painted_only_where_a_frame_lands_in_it(self):
        target = ATarget(5)
        target.ensure(640, 360)

        Showing(_frames(), gpu=AGpu()).show_newest(target)

        assert target.painted is False

    def test_the_relay_hears_the_copy_is_done_only_once_the_gpu_has_finished_it(self):
        gpu, relay = AGpu(reaches_marks=False), FrameRelay()
        relay.painted(1, texture=21, width=640, height=360, video="c.mp4")
        showing = Showing(_frames(relay), gpu=gpu)
        showing.show_newest(ATarget(5))
        relay.painted(2, texture=22, width=640, height=360, video="c.mp4")

        assert showing.show_newest(ATarget(5)) is False
        gpu.reaches_marks = True
        assert showing.show_newest(ATarget(5)) is True
        assert gpu.copied[-1] == (22, 25)

    def test_closing_lets_go_of_the_copys_mark(self):
        gpu, relay = AGpu(), FrameRelay()
        relay.painted(1, texture=21, width=640, height=360, video="c.mp4")
        showing = Showing(_frames(relay), gpu=gpu)
        showing.show_newest(ATarget(5))

        showing.close()
        showing.close()

        assert gpu.closed_marks == 1


def test_the_engine_stand_in_answers_only_what_the_headsets_engine_does():
    answered = {"video_dims", "has_frame_to_draw", "position_ms", "render"}

    assert answered <= set(dir(HeadsetEngine))
