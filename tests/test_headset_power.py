"""Fun Time wakes the headset's runtime as it opens and puts it to sleep as it
closes, so that entering VR does not wait out the runtime's own startup."""
from __future__ import annotations

from unittest.mock import patch

from fun_time import headset_power
from fun_time_vr.vr_runtime import Probe, Readiness
from fun_time_vr.windows_sound import SoundDevice

SPEAKERS = SoundDevice("{0.0.0.00000000}.{speakers}", "Speakers (Example Audio)")
HEADSET = SoundDevice("{0.0.0.00000000}.{headset}", "Headphones (Example Headset)")
OUTPUT_FOR_MUSIC = ("output", "multimedia")


class _Threads:
    def __init__(self) -> None:
        self.started: list[tuple] = []

    def __call__(self, *, target, args=(), name=""):
        self.started.append((name, target, args))


def _woken(tmp_path, *, before=None, during=None, ready=True, already_up=False):
    found = Probe(Readiness.READY if ready else Readiness.NO_HEADSET, "")
    with (
        patch("fun_time_vr.windows_sound.default_devices",
              side_effect=[before or {}, during or {}]),
        patch("fun_time_vr.vr_runtime.what_the_runtime_moved_goes_back") as put_back,
        patch("fun_time_vr.vr_runtime.remember_the_sound_devices") as remembered,
        patch("fun_time_vr.vr_runtime.runtime_was_running", return_value=already_up),
        patch("fun_time_vr.vr_runtime.ensure_ready", return_value=found),
    ):
        answered = headset_power.get_the_headset_ready(tmp_path)
    return answered, put_back, remembered


class TestWakingItAsFunTimeOpens:
    def test_the_wake_runs_off_the_session_s_own_startup(self, tmp_path):
        """He said outright that he does not mind Fun Time coming up before the
        headset is ready, and the room must not wait on it."""
        threads = _Threads()

        headset_power.wake_the_headset(tmp_path, start_thread=threads)

        assert [name for name, _, _ in threads.started] == ["headset-wake"]
        assert threads.started[0][2] == (tmp_path,)

    def test_a_test_run_wakes_no_headset(self, tmp_path):
        """The integration suite shares this machine with him: it reaches
        nothing of his, and the headset is his."""
        threads = _Threads()

        headset_power.wake_the_headset(tmp_path, integration=True, start_thread=threads)

        assert threads.started == []

    def test_it_reports_what_the_runtime_came_up_as(self, tmp_path):
        answered, _, _ = _woken(tmp_path)

        assert answered.readiness is Readiness.READY

    def test_his_sound_goes_back_where_the_runtime_moved_it(self, tmp_path):
        """The runtime makes the headset Windows' default as it comes up, and he
        is still at his desk: a VR session names its own output, so nothing
        needs the default left on the headset."""
        _, put_back, _ = _woken(
            tmp_path, before={OUTPUT_FOR_MUSIC: SPEAKERS}, during={OUTPUT_FOR_MUSIC: HEADSET})

        put_back.assert_called_once_with(
            {OUTPUT_FOR_MUSIC: SPEAKERS},
            {OUTPUT_FOR_MUSIC: HEADSET},
            {OUTPUT_FOR_MUSIC: HEADSET})

    def test_what_was_default_is_written_down_for_the_quit(self, tmp_path):
        _, _, remembered = _woken(tmp_path, before={OUTPUT_FOR_MUSIC: SPEAKERS})

        assert remembered.call_args.args[1] == {OUTPUT_FOR_MUSIC: SPEAKERS}

    def test_a_runtime_he_started_himself_is_not_written_down_again(self, tmp_path):
        _, _, remembered = _woken(tmp_path, already_up=True)

        remembered.assert_not_called()

    def test_it_says_the_headset_is_ready(self, tmp_path):
        _woken(tmp_path)

        assert headset_power.the_headset_is_ready(tmp_path) is True

    def test_it_says_nothing_of_a_headset_that_never_answered(self, tmp_path):
        _woken(tmp_path, ready=False)

        assert headset_power.the_headset_is_ready(tmp_path) is False

    def test_a_runtime_it_woke_is_fun_time_s_to_put_to_sleep(self, tmp_path):
        _woken(tmp_path)

        assert headset_power.the_headset_runtime_is_ours(tmp_path) is True

    def test_a_runtime_he_started_himself_stays_his(self, tmp_path):
        """His own Pimax, up for another VR app before Fun Time opened: Fun
        Time uses it and never puts it to sleep."""
        _woken(tmp_path, already_up=True)

        assert headset_power.the_headset_runtime_is_ours(tmp_path) is False


class TestPuttingItToSleepAsFunTimeCloses:
    def _closed(self, tmp_path, *, crossing, ours):
        if ours:
            _woken(tmp_path)
        with patch("fun_time_vr.vr_runtime.stop_the_runtime_a_session_started") as stopped:
            slept = headset_power.put_the_headset_to_sleep(tmp_path, crossing=crossing)
        return slept, stopped

    def test_a_runtime_fun_time_woke_is_put_to_sleep(self, tmp_path):
        slept, stopped = self._closed(tmp_path, crossing=False, ours=True)

        assert slept is True
        stopped.assert_called_once_with(tmp_path)

    def test_the_ready_word_goes_with_it(self, tmp_path):
        """A flag left behind would tell the next session the headset is up
        when the runtime it belonged to has gone."""
        self._closed(tmp_path, crossing=False, ours=True)

        assert headset_power.the_headset_is_ready(tmp_path) is False
        assert headset_power.the_headset_runtime_is_ours(tmp_path) is False

    def test_a_session_crossing_into_vr_leaves_it_awake(self, tmp_path):
        """The session arriving in the headset is about to draw in it."""
        slept, stopped = self._closed(tmp_path, crossing=True, ours=True)

        assert slept is False
        stopped.assert_not_called()

    def test_a_runtime_he_started_himself_is_left_awake(self, tmp_path):
        slept, stopped = self._closed(tmp_path, crossing=False, ours=False)

        assert slept is False
        stopped.assert_not_called()
