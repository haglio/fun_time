from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from fun_time.bridge_records import Op, WindowOp
from fun_time.rfb_modes import RfbMode
from fun_time.session_environment import SessionEnvironment
from fun_time.shared_state import BridgeState
from fun_time.shortcuts import Shortcut
from fun_time.window_work import WINDOW_OPS
from tests.fun_time_window_fakes import RFB_HWND
from tests.loop_parts import WindowsThatWriteDownWhatTheyAreAsked, made_up_window_work

HOSTING = BridgeState(rfb_mode=RfbMode.ORIGENERATOR)
PAUSED_WHILE_HOSTING = replace(HOSTING, omni_paused=True)


@pytest.mark.parametrize(("op", "state", "asked"), [
    (WindowOp(op=Op.SHOW_WINDOW, key="portrait"), HOSTING, ("show", "portrait")),
    (WindowOp(op=Op.HIDE_WINDOW, key="portrait"), HOSTING, ("hide_after_settle", "portrait")),
    (WindowOp(op=Op.ACTIVATE_WINDOW, key="rfb"), HOSTING, ("activate", "rfb")),
    (WindowOp(op=Op.MINIMIZE_WINDOW, key="landscape"), HOSTING, ("park", "landscape")),
    (WindowOp(op=Op.RESTORE_PARKED), HOSTING, ("restore_parked",)),
    (WindowOp(op=Op.RESTACK_RFB), PAUSED_WHILE_HOSTING,
     ("restack_rfb_mode", RfbMode.ORIGENERATOR, True)),
    (WindowOp(op=Op.DISABLE_ALL_TOPMOST), HOSTING, ("remove_all_topmost",)),
    (WindowOp(op=Op.RESTORE_ALL_TOPMOST), HOSTING, ("restore_all_topmost", RfbMode.ORIGENERATOR)),
])
def test_each_window_op_asks_the_windows_for_its_own_move(tmp_path, op, state, asked):
    windows = WindowsThatWriteDownWhatTheyAreAsked()

    made_up_window_work(tmp_path, windows=windows).run(op, state)

    assert windows.asked == [asked]


def test_every_window_op_is_one_the_table_above_or_the_tab_queue_tries():
    tried = {Op.SHOW_WINDOW, Op.HIDE_WINDOW, Op.ACTIVATE_WINDOW, Op.MINIMIZE_WINDOW,
             Op.RESTORE_PARKED, Op.RESTACK_RFB, Op.DISABLE_ALL_TOPMOST, Op.RESTORE_ALL_TOPMOST,
             Op.OPEN_RFB_TAB}

    assert set(WINDOW_OPS) == tried


def test_an_integration_run_never_takes_the_foreground_for_a_window(tmp_path):
    windows = WindowsThatWriteDownWhatTheyAreAsked()
    work = made_up_window_work(tmp_path, windows=windows, env=SessionEnvironment(integration=True))

    work.run(WindowOp(op=Op.ACTIVATE_WINDOW, key="rfb"), BridgeState())

    assert windows.asked == []


def test_omniminimize_minimizes_the_windows_the_browsers_mode_shows(tmp_path):
    windows = WindowsThatWriteDownWhatTheyAreAsked()

    made_up_window_work(tmp_path, windows=windows).minimize_all(BridgeState())

    (move, names), = windows.asked
    assert move == "minimize_all"
    assert "main_funestra" in names


class TestTheTabsWaitingForTheBrowser:
    def _work(self, tmp_path, opened: list):
        work = made_up_window_work(
            tmp_path, windows=WindowsThatWriteDownWhatTheyAreAsked(rfb_hwnd=RFB_HWND),
            rfb_shortcut=Shortcut(target="chrome.exe", work_dir="", arguments=""))
        return work, (
            patch("fun_time.window_work.window_exists", return_value=True),
            patch("fun_time.window_work.force_foreground_window", return_value=True),
            patch("fun_time.window_work.open_rfb_tab",
                  side_effect=lambda **asked: opened.append(asked["urls"])),
        )

    def test_a_tab_asked_for_waits_until_the_tabs_are_flushed(self, tmp_path):
        opened: list = []
        work, (exists, foreground, open_tab) = self._work(tmp_path, opened)

        with exists, foreground, open_tab:
            work.run(WindowOp(op=Op.OPEN_RFB_TAB, key="https://example.com/one"), BridgeState())
            waiting = list(opened)
            work.flush_rfb_tabs(BridgeState())

        assert waiting == []
        assert opened == [["https://example.com/one"]]
        assert work.pending_rfb_urls == []

    def test_the_tabs_of_one_tick_open_in_one_chrome_launch(self, tmp_path):
        opened: list = []
        work, (exists, foreground, open_tab) = self._work(tmp_path, opened)

        with exists, foreground, open_tab:
            with work.holding_a_ticks_tabs_for_one_chrome_launch():
                for url in ("https://example.com/one", "https://example.com/two"):
                    work.run(WindowOp(op=Op.OPEN_RFB_TAB, key=url), BridgeState())
                    work.flush_rfb_tabs_unless_a_tick_holds_them(BridgeState())
            work.flush_rfb_tabs_unless_a_tick_holds_them(BridgeState())

        assert opened == [["https://example.com/one", "https://example.com/two"]]

    def test_tabs_asked_for_while_origenerator_core_has_the_browsers_place_keep_waiting(
            self, tmp_path):
        opened: list = []
        work, (exists, foreground, open_tab) = self._work(tmp_path, opened)
        work.pending_rfb_urls = ["file:///tab.html"]

        with exists, foreground, open_tab:
            work.flush_rfb_tabs(HOSTING)

        assert opened == []
        assert work.pending_rfb_urls == ["file:///tab.html"]


class TestOrigeneratorWindowConverger:
    def test_no_converge_undoes_where_omnipause_put_the_hosted_window(self, tmp_path):
        windows = WindowsThatWriteDownWhatTheyAreAsked()

        made_up_window_work(tmp_path, windows=windows).converge_origenerator_window(
            PAUSED_WHILE_HOSTING)

        assert windows.asked == []

    def test_outside_omnipause_the_windows_are_asked_for_the_browsers_place(self, tmp_path):
        windows = WindowsThatWriteDownWhatTheyAreAsked()

        made_up_window_work(tmp_path, windows=windows).converge_origenerator_window(HOSTING)

        assert windows.asked == [("converge_origenerator_window", RfbMode.ORIGENERATOR)]
