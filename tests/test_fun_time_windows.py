from __future__ import annotations

from functools import partial
from unittest.mock import patch

from shared_ui.preview import Preview, window_title

from fun_time.fun_time_windows import (
    MAIN_BLANK_SETTLE_S,
    ChildPids,
    FunTimeWindows,
)
from fun_time.origenerator_switches import RfbMode
from fun_time.window_layout import WindowRect
from fun_time.window_names import ORIGENERATOR_TITLE, ORIGENERATOR_WINDOW
from fun_time.windows_bridge_sequencer import apply_topmost_bands
from fun_time.windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)
from tests.fun_time_window_fakes import (
    DASHBOARD_HWND,
    DASHBOARD_PID,
    HOSTED_HWND,
    HOSTED_PID,
    LANDSCAPE_HWND,
    LANDSCAPE_PID,
    MAIN_FUNESTRA_HWND,
    MAIN_FUNESTRA_PID,
    PORTRAIT_HWND,
    PORTRAIT_PID,
    RFB_HWND,
    TOPMOST_HWNDS,
    HandDrivenClock,
    lookup_hosted,
    lookup_pid,
    lookup_title,
)


def _minimized(answer: bool):
    return patch("fun_time.fun_time_windows.is_window_minimized", return_value=answer)


def make_windows(**overrides) -> FunTimeWindows:
    pids = {"main_funestra": MAIN_FUNESTRA_PID, "portrait": PORTRAIT_PID,
                "landscape": LANDSCAPE_PID, "dashboard": DASHBOARD_PID}
    pids.update(overrides.pop("pids", {}))
    return FunTimeWindows(pids=ChildPids(**pids), **overrides)


def test_a_window_hidden_since_startup_is_reached_by_its_startup_handle():
    windows = FunTimeWindows(pids=ChildPids(main_funestra=MAIN_FUNESTRA_PID),
                             hwnds_seen_while_visible={"main_funestra": MAIN_FUNESTRA_HWND})

    with patch("fun_time.fun_time_windows.find_window_by_pid", return_value=0),          patch("fun_time.fun_time_windows.find_window_by_title", return_value=0):
        assert windows.hwnd("main_funestra") == MAIN_FUNESTRA_HWND


def test_with_no_pids_and_no_window_found_every_window_op_is_a_no_op():
    windows = FunTimeWindows(pids=ChildPids())

    with patch("fun_time.fun_time_windows.find_window_by_pid", return_value=0), \
         patch("fun_time.fun_time_windows.find_window_by_title", return_value=0), \
         patch("fun_time.fun_time_windows.find_window_for_process", return_value=0), \
         patch("fun_time.fun_time_windows.restore_window") as restore, \
         patch("fun_time.fun_time_windows.activate_window") as activate, \
         patch("fun_time.fun_time_windows.minimize_window") as minimize, \
         patch("fun_time.fun_time_windows.set_always_on_top") as topmost:
        windows.show("main_funestra")
        windows.activate("portrait")
        windows.minimize("landscape")
        windows.park("dashboard")
        windows.remove_all_topmost()

    assert [op.call_count for op in (restore, activate, minimize, topmost)] == [0, 0, 0, 0]


class TestResolveRole:
    def test_main_funestra_falls_back_to_exact_title_when_pid_fails(self):
        windows = make_windows()

        title_calls: list[tuple[str, bool]] = []

        def title_lookup(title, exact=False):
            title_calls.append((title, exact))
            return 2002 if (title == "Main Funestra" and exact) else 0

        with patch("fun_time.fun_time_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=title_lookup):
            hwnd = windows.hwnd("main_funestra")

        assert ("Main Funestra", True) in title_calls, "must try the exact-title fallback"
        assert hwnd == 2002

    def test_dashboard_falls_back_to_title_when_pid_fails(self):
        windows = make_windows()

        def title_lookup(title, exact=False):
            return 9999 if title == "Fun Time" else 0

        with patch("fun_time.fun_time_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=title_lookup):
            assert windows.hwnd("dashboard") == 9999

    def test_each_satellite_falls_back_to_its_own_exact_caption(self):
        windows = make_windows()

        def title_lookup(title, exact=False):
            if not exact:
                return 0
            return {SATELLITE_PORTRAIT_TITLE: PORTRAIT_HWND,
                    SATELLITE_LANDSCAPE_TITLE: LANDSCAPE_HWND}.get(title, 0)

        with patch("fun_time.fun_time_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=title_lookup):
            assert windows.hwnd("portrait") == PORTRAIT_HWND
            assert windows.hwnd("landscape") == LANDSCAPE_HWND

    def test_a_dead_hosted_window_handle_is_dropped_and_re_resolved(self):
        windows = make_windows(pids={"origenerator": HOSTED_PID})

        with patch("fun_time.fun_time_windows.find_window_for_process",
                   return_value=8888):
            assert windows.hwnd("origenerator") == 8888

        with patch("fun_time.fun_time_windows.window_exists", return_value=False), \
             patch("fun_time.fun_time_windows.find_window_for_process",
                   side_effect=lookup_hosted):
            assert windows.hwnd("origenerator") == HOSTED_HWND

    def test_an_origenerator_he_had_open_as_a_preview_is_found_after_the_app_takes_it_over(self):
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        the_preview = partial(lookup_hosted, caption=window_title(
            ORIGENERATOR_TITLE, Preview(feature="a fabricated feature")))

        with patch("fun_time.fun_time_windows.find_window_for_process", side_effect=the_preview):
            assert windows.hwnd("origenerator") == HOSTED_HWND


class TestParking:
    def test_a_mode_switch_holds_its_outgoing_funestra_up_for_the_settle(self):
        clock = HandDrivenClock()
        windows = make_windows(clock=clock, hwnds_seen_while_visible={"main_funestra": MAIN_FUNESTRA_HWND})

        minimized: list[int] = []
        with patch("fun_time.fun_time_windows.minimize_window",
                   side_effect=lambda h, **kw: minimized.append(h)):
            windows.hide_after_settle("main_funestra")
            windows.flush_pending_hides()
            assert minimized == [], "the Main Funestra minimized before it could paint the black"

            clock.advance(MAIN_BLANK_SETTLE_S)
            windows.flush_pending_hides()

        assert minimized == [MAIN_FUNESTRA_HWND]

    def test_the_settle_is_generous_next_to_the_two_frames_a_window_needs(self):
        two_frames_at_60_hz_s = 2 / 60

        assert 5 * two_frames_at_60_hz_s <= MAIN_BLANK_SETTLE_S


class TestTopmostBands:
    def _promotions(self, windows, method, **kwargs) -> list[tuple[int, bool]]:
        calls: list[tuple[int, bool]] = []
        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.find_window_for_process", side_effect=lookup_hosted), \
             patch("fun_time.fun_time_windows.set_always_on_top",
                   side_effect=lambda h, v: calls.append((h, v))):
            getattr(windows, method)(**kwargs)
        return calls

    @staticmethod
    def _startup_bands(rfb_mode) -> dict[int, bool]:
        hwnds_by_window = {
            "portrait": PORTRAIT_HWND, "landscape": LANDSCAPE_HWND, "dashboard": DASHBOARD_HWND,
            "main_funestra": MAIN_FUNESTRA_HWND, "rfb": RFB_HWND, ORIGENERATOR_WINDOW: HOSTED_HWND,
        }
        bands: dict[int, bool] = {}
        with patch("fun_time.windows_bridge_sequencer.set_always_on_top",
                   side_effect=lambda hwnd, on, *, under=0: bands.update({hwnd: on})):
            apply_topmost_bands(hwnds_by_window, rfb_mode=rfb_mode)
        return bands

    def test_startup_leaving_omnipause_and_a_mode_switch_agree_on_every_band(self):
        for rfb_mode in RfbMode:
            windows = make_windows(rfb_hwnd=RFB_HWND, pids={"origenerator": HOSTED_PID})
            startup_bands = self._startup_bands(rfb_mode)

            for later in ("restore_all_topmost", "restack_rfb_mode"):
                disagreements = [
                    (later, hwnd, on)
                    for hwnd, on in self._promotions(windows, later, rfb_mode=rfb_mode)
                    if startup_bands[hwnd] is not on]
                assert disagreements == [], rfb_mode

    def test_remove_all_topmost_drops_every_managed_window(self):
        windows = make_windows(rfb_hwnd=RFB_HWND)

        calls = self._promotions(windows, "remove_all_topmost")

        assert {h for h, on in calls if on is False} == TOPMOST_HWNDS

    def test_restore_all_topmost_floats_every_window_with_a_rect_of_its_own(self):
        windows = make_windows(rfb_hwnd=RFB_HWND)

        calls = self._promotions(windows, "restore_all_topmost", rfb_mode=RfbMode.RFB)

        assert {h for h, on in calls if on is True} == TOPMOST_HWNDS

    def test_restore_all_topmost_leaves_the_browser_under_the_hosted_app(self):
        windows = make_windows(rfb_hwnd=RFB_HWND, pids={"origenerator": HOSTED_PID})

        calls = self._promotions(windows, "restore_all_topmost",
                                 rfb_mode=RfbMode.ORIGENERATOR)

        promoted = [h for h, on in calls if on]
        assert RFB_HWND not in promoted, (
            "the browser was promoted into the topmost band while the hosted "
            "app owns its rect, which puts it over Origenerator until the next "
            "promotion pushes it back down"
        )
        assert {PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND, MAIN_FUNESTRA_HWND,
                HOSTED_HWND} <= set(promoted)

    def test_a_restack_while_paused_leaves_the_topmost_band_to_the_pause(self):
        windows = make_windows(rfb_hwnd=RFB_HWND, pids={"origenerator": HOSTED_PID})
        assert self._promotions(windows, "restack_rfb_mode",
                                rfb_mode=RfbMode.ORIGENERATOR, paused=True) == []

    def test_origenerator_in_the_browsers_place_takes_the_browser_out_of_the_band(self):
        windows = make_windows(rfb_hwnd=RFB_HWND, pids={"origenerator": HOSTED_PID})

        calls = self._promotions(windows, "restack_rfb_mode",
                                 rfb_mode=RfbMode.ORIGENERATOR)

        assert calls == [(RFB_HWND, False), (HOSTED_HWND, True)]

    def test_the_browser_back_in_its_place_goes_back_in_the_band(self):
        windows = make_windows(rfb_hwnd=RFB_HWND, pids={"origenerator": HOSTED_PID})

        calls = self._promotions(windows, "restack_rfb_mode",
                                 rfb_mode=RfbMode.RFB)

        assert calls == [(RFB_HWND, True)]


class TestOrigeneratorWindowConverger:
    def test_origenerator_resumed_in_the_browsers_place_restores_the_window_once_it_exists(
            self):
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        with patch.object(windows, "hwnd", return_value=0), \
             patch("fun_time.fun_time_windows.restore_window") as restore:
            windows.converge_origenerator_window(RfbMode.ORIGENERATOR)
        restore.assert_not_called()
        with patch.object(windows, "hwnd", return_value=4242), \
             _minimized(True), \
             patch("fun_time.fun_time_windows.restore_window") as restore, \
             patch("fun_time.fun_time_windows.set_always_on_top"):
            windows.converge_origenerator_window(RfbMode.ORIGENERATOR)
        restore.assert_called_once_with(4242, activate=False)

    def test_a_restore_the_busy_app_dropped_is_retried_next_pass(self):
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        with patch.object(windows, "hwnd", return_value=4242), \
             _minimized(True), \
             patch("fun_time.fun_time_windows.restore_window") as restore, \
             patch("fun_time.fun_time_windows.set_always_on_top"):
            windows.converge_origenerator_window(RfbMode.ORIGENERATOR)
            windows.converge_origenerator_window(RfbMode.ORIGENERATOR)
        assert restore.call_count == 2

    def test_a_shown_window_out_of_the_band_is_re_promoted(self):
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        with patch.object(windows, "hwnd", return_value=4242), \
             _minimized(False), \
             patch("fun_time.fun_time_windows.is_window_topmost", return_value=False), \
             patch("fun_time.fun_time_windows.restore_window") as restore, \
             patch("fun_time.fun_time_windows.set_always_on_top") as promote:
            windows.converge_origenerator_window(RfbMode.ORIGENERATOR)
        restore.assert_not_called()
        promote.assert_any_call(4242, True)

    def test_a_browser_put_back_in_the_band_is_taken_out_from_under_origenerator(self):
        windows = make_windows(rfb_hwnd=RFB_HWND, pids={"origenerator": HOSTED_PID})
        with patch("fun_time.fun_time_windows.find_window_for_process", side_effect=lookup_hosted), \
             _minimized(False), \
             patch("fun_time.fun_time_windows.is_window_topmost", return_value=True), \
             patch("fun_time.fun_time_windows.set_always_on_top") as band:
            windows.converge_origenerator_window(RfbMode.ORIGENERATOR)
        assert band.call_args_list == [((RFB_HWND, False),), ((HOSTED_HWND, True),)]

    def test_the_browser_in_its_place_parks_a_window_left_up(self):
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        with patch.object(windows, "hwnd", return_value=4242), \
             _minimized(False), \
             patch("fun_time.fun_time_windows.minimize_window") as minimize:
            windows.converge_origenerator_window(RfbMode.RFB)
        minimize.assert_called_once_with(4242, activate_the_next_window=False)

    def test_without_a_hosted_app_the_converger_is_inert(self):
        windows = make_windows()
        with patch.object(windows, "hwnd") as resolve:
            windows.converge_origenerator_window(RfbMode.ORIGENERATOR)
        resolve.assert_not_called()


TOP_MODE_SELECTOR = WindowRect(2560, 0, 1440, 940)


def test_placing_a_window_moves_it_onto_its_rect():
    windows = make_windows(hwnds_seen_while_visible={"portrait": PORTRAIT_HWND})

    with patch("fun_time.fun_time_windows.window_rect", return_value=(2560, 0, 1440, 2500)), \
         _minimized(False), \
         patch("fun_time.fun_time_windows.place_window") as place:
        windows.place([("portrait", TOP_MODE_SELECTOR)])

    place.assert_called_once_with(PORTRAIT_HWND, 2560, 0, 1440, 940)


def test_a_window_already_on_its_rect_is_left_alone():
    windows = make_windows(hwnds_seen_while_visible={"portrait": PORTRAIT_HWND})

    with patch("fun_time.fun_time_windows.window_rect", return_value=(2560, 0, 1440, 940)), \
         _minimized(False), \
         patch("fun_time.fun_time_windows.place_window") as place:
        windows.place([("portrait", TOP_MODE_SELECTOR)])

    place.assert_not_called()


def test_a_minimized_window_is_left_where_it_was_until_it_is_back():
    windows = make_windows(hwnds_seen_while_visible={"portrait": PORTRAIT_HWND})

    with patch("fun_time.fun_time_windows.window_rect", return_value=(-32000, -32000, 160, 28)), \
         _minimized(True), \
         patch("fun_time.fun_time_windows.place_window") as place:
        windows.place([("portrait", TOP_MODE_SELECTOR)])

    place.assert_not_called()


def test_a_window_nobody_has_found_yet_is_not_placed():
    windows = make_windows()

    with patch("fun_time.fun_time_windows.find_window_by_pid", return_value=0), \
         patch("fun_time.fun_time_windows.find_window_by_title", return_value=0), \
         patch("fun_time.fun_time_windows.window_rect", return_value=None), \
         _minimized(False), \
         patch("fun_time.fun_time_windows.place_window") as place:
        windows.place([("portrait", TOP_MODE_SELECTOR)])

    place.assert_not_called()


def test_the_window_that_shrinks_moves_before_the_one_that_grows_into_its_app():
    windows = make_windows(hwnds_seen_while_visible={"portrait": PORTRAIT_HWND, "main_funestra": MAIN_FUNESTRA_HWND})
    now = {PORTRAIT_HWND: (2560, 0, 1440, 2500), MAIN_FUNESTRA_HWND: (2560, 2500, 1440, 940)}

    with patch("fun_time.fun_time_windows.window_rect", side_effect=now.get), \
         _minimized(False), \
         patch("fun_time.fun_time_windows.place_window") as place:
        windows.place([("main_funestra", WindowRect(2560, 940, 1440, 2500)), ("portrait", TOP_MODE_SELECTOR)])

    assert [call.args[0] for call in place.call_args_list] == [PORTRAIT_HWND, MAIN_FUNESTRA_HWND]


class TestParkedAndMinimized:
    def _windows(self) -> FunTimeWindows:
        return make_windows(
            clock=HandDrivenClock(),
            hwnds_seen_while_visible={"main_funestra": MAIN_FUNESTRA_HWND, "portrait": PORTRAIT_HWND},
        )

    def _restored_by(self, action) -> list[int]:
        restored: list[int] = []
        with patch("fun_time.fun_time_windows.minimize_window"), \
             patch("fun_time.fun_time_windows.restore_window",
                   side_effect=lambda h, **kw: restored.append(h)):
            action()
        return restored

    def test_showing_a_window_cancels_the_minimize_waiting_on_its_settle(self):
        clock = HandDrivenClock()
        windows = make_windows(clock=clock, hwnds_seen_while_visible={"main_funestra": MAIN_FUNESTRA_HWND})
        minimized: list[int] = []

        with patch("fun_time.fun_time_windows.minimize_window",
                   side_effect=lambda h, **kw: minimized.append(h)), \
             patch("fun_time.fun_time_windows.restore_window"):
            windows.hide_after_settle("main_funestra")
            windows.show("main_funestra")
            clock.advance(MAIN_BLANK_SETTLE_S)
            windows.flush_pending_hides()

        assert minimized == []

    def test_a_parked_satellite_keeps_its_taskbar_button_as_the_way_back(self):
        windows = self._windows()

        with patch("fun_time.fun_time_windows.minimize_window") as minimize:
            windows.park("portrait")

        minimize.assert_called_once_with(PORTRAIT_HWND, activate_the_next_window=False)

    def test_a_window_a_mode_minimizes_is_not_one_the_next_resume_restores(self):
        windows = self._windows()

        def switch_then_resume():
            with patch("fun_time.fun_time_windows.minimize_window"):
                windows.minimize("main_funestra")
            windows.restore_parked()

        assert self._restored_by(switch_then_resume) == []

    def test_a_window_a_button_parks_is_restored_once_however_often_it_was_parked(self):
        windows = self._windows()

        def park_twice_then_resume_twice():
            windows.park("portrait")
            windows.park("portrait")
            windows.restore_parked()
            windows.restore_parked()

        assert self._restored_by(park_twice_then_resume_twice) == [PORTRAIT_HWND]

    def test_restoring_the_minimized_windows_leaves_no_parked_one_to_restore_again(self):
        windows = self._windows()

        def minimize_all_then_come_back():
            windows.park("portrait")
            windows.minimize_all(["portrait", "main_funestra"])
            windows.restore_minimized()
            windows.restore_parked()

        assert self._restored_by(minimize_all_then_come_back) == [PORTRAIT_HWND, MAIN_FUNESTRA_HWND]
