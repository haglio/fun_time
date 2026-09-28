from __future__ import annotations

from fun_time_vr.paint_watch import (
    DONE,
    FLUSH,
    FLUSH_AFTER_S,
    GIVE_UP,
    GIVE_UP_AFTER_S,
    NO_PICTURE_AFTER_S,
    READ_EVERY_S,
    WAIT,
    GpuWait,
    PictureWatch,
)


class TestWaitingForTheGpu:
    def test_a_picture_the_gpu_has_finished_is_done_at_once(self):
        assert GpuWait().judge(reached=True, now=0.0) == DONE

    def test_a_picture_still_on_the_gpu_is_waited_for(self):
        wait = GpuWait()

        assert [wait.judge(reached=False, now=t) for t in (0.0, 0.3, FLUSH_AFTER_S * 0.9)] == [
            WAIT, WAIT, WAIT]

    def test_one_that_has_waited_a_second_gets_one_flush_and_then_more_waiting(self):
        wait = GpuWait()
        wait.judge(reached=False, now=0.0)

        assert wait.judge(reached=False, now=FLUSH_AFTER_S) == FLUSH
        assert wait.judge(reached=False, now=FLUSH_AFTER_S + 0.1) == WAIT

    def test_one_the_flush_finished_is_done(self):
        wait = GpuWait()
        wait.judge(reached=False, now=0.0)
        wait.judge(reached=False, now=FLUSH_AFTER_S)

        assert wait.judge(reached=True, now=FLUSH_AFTER_S + 0.1) == DONE

    def test_one_that_never_finishes_is_given_up_on_and_the_next_wait_starts_afresh(self):
        wait = GpuWait()
        wait.judge(reached=False, now=0.0)
        wait.judge(reached=False, now=FLUSH_AFTER_S)

        assert wait.judge(reached=False, now=GIVE_UP_AFTER_S) == GIVE_UP
        assert wait.judge(reached=False, now=GIVE_UP_AFTER_S + 0.1) == WAIT
        assert wait.judge(reached=False, now=GIVE_UP_AFTER_S + 0.1 + FLUSH_AFTER_S) == FLUSH


class TestWatchingForPictures:
    def _gap(self, watch, *readings):
        return [watch.read(position_ms=position, now=now) for position, now in readings]

    def test_a_video_that_keeps_drawing_asks_for_a_reading_only_once_a_gap_starts(self):
        watch = PictureWatch()
        watch.drew()

        assert watch.wants_a_reading(now=0.0)
        watch.read(position_ms=0.0, now=0.0)
        assert not watch.wants_a_reading(now=READ_EVERY_S * 0.5)
        assert watch.wants_a_reading(now=READ_EVERY_S)

    def test_a_video_that_played_on_with_nothing_drawn_is_said_once_with_the_gaps_length(self):
        watch = PictureWatch()

        assert self._gap(watch, (1_000.0, 0.0), (4_000.0, NO_PICTURE_AFTER_S * 0.9),
                         (7_000.0, NO_PICTURE_AFTER_S), (9_000.0, NO_PICTURE_AFTER_S * 3)) == [
            None, None, NO_PICTURE_AFTER_S, None]

    def test_a_video_holding_still_is_not_a_gap(self):
        """Paused, or a file still opening: no picture is owed while nothing plays."""
        watch = PictureWatch()

        assert self._gap(watch, (1_000.0, 0.0), (1_000.0, NO_PICTURE_AFTER_S * 5)) == [None, None]

    def test_a_picture_ends_the_gap_and_a_new_gap_is_said_again(self):
        watch = PictureWatch()
        self._gap(watch, (1_000.0, 0.0), (7_000.0, NO_PICTURE_AFTER_S))

        watch.drew()

        assert self._gap(watch, (8_000.0, 10.0), (20_000.0, 10.0 + NO_PICTURE_AFTER_S)) == [
            None, NO_PICTURE_AFTER_S]
