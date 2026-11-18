from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from types import MappingProxyType

from app_support.win32 import force_foreground_window, window_exists

from .bridge_records import BridgeConfig, Op, WindowOp
from .dispatch_loop_setup import SessionFiles, SessionStart
from .rfb_modes import RfbMode
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .shared_state import BridgeState
from .shortcuts import Shortcut
from .window_names import visible_windows
from .windows_bridge_random_faves_browser import open_rfb_tab

logger = logging.getLogger(__name__)


class WindowWork:
    def __init__(self, config: BridgeConfig, files: SessionFiles, start: SessionStart,
                 env: SessionEnvironment = ORDINARY_SESSION) -> None:
        self.windows = start.windows
        self.rfb_shortcut = start.rfb_shortcut or Shortcut()
        self.pending_rfb_urls: list[str] = []
        self._config = config
        self._ahk_cmd_file = files.ahk_cmd
        self._rfb_slideshow = start.rfb_slideshow
        self.env = env
        self._a_tick_holds_its_tabs_for_one_chrome_launch = False

    def run(self, op: WindowOp, state: BridgeState) -> None:
        WINDOW_OPS[op.op](self, op, state)

    def minimize_all(self, state: BridgeState) -> None:
        self.windows.minimize_all(visible_windows(state.rfb_mode))

    @contextlib.contextmanager
    def holding_a_ticks_tabs_for_one_chrome_launch(self) -> Iterator[None]:
        self._a_tick_holds_its_tabs_for_one_chrome_launch = True
        try:
            yield
        finally:
            self._a_tick_holds_its_tabs_for_one_chrome_launch = False

    def flush_rfb_tabs_unless_a_tick_holds_them(self, state: BridgeState) -> None:
        if not self._a_tick_holds_its_tabs_for_one_chrome_launch:
            self.flush_rfb_tabs(state)

    def flush_rfb_tabs(self, state: BridgeState) -> None:
        # Chrome's FindTabbedBrowser walks the profile's windows most-recently-active first, so the
        # RFB window is activated first.
        if state.rfb_mode is RfbMode.ORIGENERATOR:
            return
        urls = self.pending_rfb_urls
        self.pending_rfb_urls = []
        if not urls or not self.rfb_shortcut.target:
            return
        no_window_of_our_own = not window_exists(self.windows.rfb_hwnd)
        if no_window_of_our_own:
            logger.warning(
                "%d RFB tab(s) skipped: no Random Faves Browser window to open into", len(urls))
            return
        if not force_foreground_window(self.windows.rfb_hwnd):
            logger.info("RFB window did not take the foreground before the tab handoff")
        open_rfb_tab(urls=urls, shortcut=self.rfb_shortcut)
        logger.info("Opened %d RFB tab(s)", len(urls))
        if self._rfb_slideshow is not None:
            self._rfb_slideshow.restart(now=time.monotonic())

    def converge_origenerator_window(self, state: BridgeState) -> None:
        the_pause_put_every_window_where_it_stands = state.omni_paused
        if the_pause_put_every_window_where_it_stands:
            return
        self.windows.converge_origenerator_window(state.rfb_mode)

    def log_topmost_state(self, label: str, state: BridgeState) -> None:
        logger.info("Topmost [%s] mode=%s: %s", label, state.main_mode,
                    self.windows.topmost_report())

    def close(self) -> None:
        if self._rfb_slideshow is not None:
            self._rfb_slideshow.stop()


def _show(work: WindowWork, op: WindowOp, _state: BridgeState) -> None:
    work.windows.show(op.key)


def _hide(work: WindowWork, op: WindowOp, _state: BridgeState) -> None:
    work.windows.hide_after_settle(op.key)


def _park(work: WindowWork, op: WindowOp, _state: BridgeState) -> None:
    work.windows.park(op.key)


def _restore_parked(work: WindowWork, _op: WindowOp, _state: BridgeState) -> None:
    work.windows.restore_parked()


def _activate(work: WindowWork, op: WindowOp, _state: BridgeState) -> None:
    if not work.env.integration:
        work.windows.activate(op.key)


def _restack_rfb(work: WindowWork, _op: WindowOp, state: BridgeState) -> None:
    work.windows.restack_rfb_mode(state.rfb_mode, paused=state.omni_paused)


def _disable_all_topmost(work: WindowWork, _op: WindowOp, _state: BridgeState) -> None:
    work.windows.remove_all_topmost()


def _restore_all_topmost(work: WindowWork, _op: WindowOp, state: BridgeState) -> None:
    work.windows.restore_all_topmost(state.rfb_mode)


def _open_rfb_tab(work: WindowWork, op: WindowOp, _state: BridgeState) -> None:
    work.pending_rfb_urls.append(op.key)


WINDOW_OPS: Mapping[str, Callable[[WindowWork, WindowOp, BridgeState], None]] = MappingProxyType({
    Op.SHOW_WINDOW: _show,
    Op.HIDE_WINDOW: _hide,
    Op.ACTIVATE_WINDOW: _activate,
    Op.MINIMIZE_WINDOW: _park,
    Op.RESTORE_PARKED: _restore_parked,
    Op.RESTACK_RFB: _restack_rfb,
    Op.DISABLE_ALL_TOPMOST: _disable_all_topmost,
    Op.RESTORE_ALL_TOPMOST: _restore_all_topmost,
    Op.OPEN_RFB_TAB: _open_rfb_tab,
})
