"""The live windows behind the managed roles.

These pin :class:`fun_time.role_windows.WindowRoles` directly, which is the
point of it having been lifted out of the dispatch loop: the object needs the
child pids, the browser's hwnd and the startup seed, and nothing else — no
config, no session state, no runner.
"""
from __future__ import annotations

from unittest.mock import patch

from fun_time.role_windows import (
    MAIN_BLANK_SETTLE_S,
    ChildPids,
    WindowRoles,
)
from fun_time.windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)
from tests.role_window_fakes import (
    DASHBOARD_HWND,
    DASHBOARD_PID,
    FakeClock,
    GENAU_HWND,
    HOSTED_LANDSCAPE_HWND,
    HOSTED_PORTRAIT_HWND,
    HOSTED_HWND,
    HOSTED_PID,
    LANDSCAPE_HWND,
    LANDSCAPE_PID,
    NAU_HWND,
    NAU_PID,
    PORTRAIT_HWND,
    PORTRAIT_PID,
    RFB_HWND,
    TOPMOST_HWNDS,
    lookup_hosted,
    lookup_pid,
    lookup_title,
)


def make_windows(**overrides) -> WindowRoles:
    pids = dict(nau=NAU_PID, portrait=PORTRAIT_PID,
                landscape=LANDSCAPE_PID, dashboard=DASHBOARD_PID)
    pids.update(overrides.pop("pids", {}))
    return WindowRoles(pids=ChildPids(**pids), **overrides)


def test_the_windows_object_needs_only_the_pids_and_the_startup_seed():
    """Startup resolves every window while it is still visible and hands the
    map over; hidden windows are invisible to the pid/title lookups, so that
    seed is the only way back to one.  Nothing else is needed to answer for a
    role, which is what lets these windows be reasoned about on their own."""
    windows = WindowRoles(pids=ChildPids(nau=NAU_PID), role_hwnds={"nau": NAU_HWND})

    assert windows.hwnd("nau") == NAU_HWND


class TestResolveRole:
    def test_nau_falls_back_to_exact_title_when_pid_fails(self):
        """The venv pythonw launcher's PID differs from the interpreter that
        owns the SDL window, so resolution must fall back to an exact-title
        lookup — exact because 'Nau' is a substring of 'Genau'."""
        windows = make_windows()

        title_calls: list[tuple[str, bool]] = []

        def title_lookup(title, exact=False):
            title_calls.append((title, exact))
            return 2002 if (title == "Nau" and exact) else 0

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=title_lookup):
            hwnd = windows.hwnd("nau")

        assert ("Nau", True) in title_calls, "must try the exact-title fallback"
        assert hwnd == 2002

    def test_dashboard_falls_back_to_title_when_pid_fails(self):
        """When find_window_by_pid cannot find the Dashboard (PID mismatch
        from the venv launcher), resolution falls back to its title."""
        windows = make_windows()

        def title_lookup(title, exact=False):
            return 9999 if title == "Fun Time" else 0

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=title_lookup):
            assert windows.hwnd("dashboard") == 9999

    def test_each_satellite_falls_back_to_its_own_exact_caption(self):
        """The recorded satellite pids are the venv launcher's, not the
        interpreter that owns the SDL window, so on a cold cache the by-pid
        lookup finds nothing and resolution falls back to the caption — each
        side's own, exactly.  The captions differ only in their first word,
        so a swapped or substring lookup here assigns one side's window to
        the other, which is the portrait/landscape visual swap."""
        windows = make_windows()

        def title_lookup(title, exact=False):
            if not exact:
                return 0
            return {SATELLITE_PORTRAIT_TITLE: PORTRAIT_HWND,
                    SATELLITE_LANDSCAPE_TITLE: LANDSCAPE_HWND}.get(title, 0)

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=title_lookup):
            assert windows.hwnd("portrait") == PORTRAIT_HWND
            assert windows.hwnd("landscape") == LANDSCAPE_HWND

    def test_a_dead_hosted_window_handle_is_dropped_and_re_resolved(self):
        """The hosted app's boot can put a short-lived twin of its caption up
        first (its splash); caching that handle would aim every later restore
        at a dead window — the mode switch that visibly does nothing.  Only
        this role heals its cache, so a dead handle must be re-resolved."""
        windows = make_windows(pids={"origenerator": HOSTED_PID})

        with patch("fun_time.role_windows.find_window_for_process",
                   return_value=8888):
            assert windows.hwnd("origenerator") == 8888  # the splash, cached

        with patch("fun_time.role_windows.window_exists", return_value=False), \
             patch("fun_time.role_windows.find_window_for_process",
                   side_effect=lookup_hosted):
            assert windows.hwnd("origenerator") == HOSTED_HWND


class TestParking:
    """Which window goes down, when, and what brings it back."""

    def test_a_mode_switch_holds_its_outgoing_player_up_for_the_settle(self):
        """Minimizing freezes a window's Alt-Tab thumbnail — Windows stops
        compositing it — so the player a switch is leaving must go down only
        once the DISPLAY_OFF sent in the same breath is on screen.  Minimize in
        the frame or two that takes and the thumbnail keeps the video frame the
        player was sitting on, which is the whole thing the blanking is for."""
        clock = FakeClock()
        windows = make_windows(clock=clock, role_hwnds={"nau": NAU_HWND})

        minimized: list[int] = []
        with patch("fun_time.role_windows.minimize_window",
                   side_effect=lambda h, **kw: minimized.append(h)):
            windows.hide_after_settle("nau")
            windows.flush_pending_hides()
            assert minimized == [], "Nau minimized before it could paint the black"

            clock.advance(MAIN_BLANK_SETTLE_S)
            windows.flush_pending_hides()

        assert minimized == [NAU_HWND]


class TestTopmostBands:
    """Which windows float, and in what order they are promoted.

    The modes are arguments, not state the object holds: these windows are the
    same windows whatever the session is doing, and the band they belong in is
    the caller's question to ask.
    """

    def _promotions(self, windows, method, **kwargs) -> list[tuple[int, bool]]:
        calls: list[tuple[int, bool]] = []
        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.find_window_for_process", side_effect=lookup_hosted), \
             patch("fun_time.role_windows.set_always_on_top",
                   side_effect=lambda h, v: calls.append((h, v))):
            getattr(windows, method)(**kwargs)
        return calls

    def test_remove_all_topmost_drops_every_managed_window(self):
        """Omnipause enter frees the desktop entirely — Nau included, so it is
        never left stranded on top."""
        windows = make_windows(rfb_hwnd=RFB_HWND)

        calls = self._promotions(windows, "remove_all_topmost")

        assert {h for h, on in calls if on is False} == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}

    def test_restore_all_topmost_floats_nau_in_nau_mode(self):
        """nau mode: Nau reclaims the topmost band, above the desktop."""
        windows = make_windows(rfb_hwnd=RFB_HWND)

        calls = self._promotions(windows, "restore_all_topmost",
                                 main_mode="nau", satellites_mode="player")

        assert {h for h, on in calls if on is True} == TOPMOST_HWNDS | {NAU_HWND}

    def test_hybrid_promotes_nau_before_genau_so_the_hud_lands_on_top(self):
        """hybrid: Nau and Genau are BOTH topmost so the composite floats above
        the desktop, and HWND_TOPMOST inserts at the TOP of the band — so Nau is
        promoted BEFORE Genau, which is what stacks the HUD over the video."""
        windows = make_windows(rfb_hwnd=RFB_HWND)

        calls = self._promotions(windows, "restore_all_topmost",
                                 main_mode="hybrid", satellites_mode="player")

        promoted = [h for h, on in calls if on]
        assert {RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND,
                NAU_HWND, GENAU_HWND} <= set(promoted)
        assert promoted.index(NAU_HWND) < promoted.index(GENAU_HWND)

    def test_restore_all_topmost_leaves_the_browser_under_the_hosted_app(self):
        """His: the Random Favs Browser flashes over Origenerator for a moment
        every time the room resumes from OmniPause.

        The browser shares its rect with the hosted app's main window, and the
        band policy already answers "not topmost" for it in origenerator mode
        — but this path promoted every fixed role without asking, so the
        browser went to the top of the band (HWND_TOPMOST inserts there) and
        stayed above Origenerator until the hosted trio's own promotion pushed
        the host back over it a moment later.  That gap is the flash.
        """
        windows = make_windows(rfb_hwnd=RFB_HWND, pids={"origenerator": HOSTED_PID})

        calls = self._promotions(windows, "restore_all_topmost",
                                 main_mode="nau", satellites_mode="origenerator")

        promoted = [h for h, on in calls if on]
        assert RFB_HWND not in promoted, (
            "the browser was promoted into the topmost band while the hosted "
            "app owns its rect, which puts it over Origenerator until the next "
            "promotion pushes it back down"
        )
        # Everything the mode really does show still comes back.
        assert {PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND, NAU_HWND,
                HOSTED_HWND, HOSTED_PORTRAIT_HWND,
                HOSTED_LANDSCAPE_HWND} <= set(promoted)


class TestOrigeneratorWindowConverger:
    """The hosted app's window is converged to what the satellites' mode says,
    read off the window each pass rather than remembered."""

    def test_a_resumed_origenerator_mode_restores_the_window_once_it_exists(self):
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        with patch.object(windows, "hwnd", return_value=0), \
             patch("fun_time.role_windows.restore_window") as restore:
            windows.converge_origenerator_window("nau", "origenerator")
        restore.assert_not_called()  # still booting — nothing to drive
        with patch.object(windows, "hwnd", return_value=4242), \
             patch("fun_time.role_windows.is_window_minimized", return_value=True), \
             patch("fun_time.role_windows.restore_window") as restore, \
             patch("fun_time.role_windows.set_always_on_top"):
            windows.converge_origenerator_window("nau", "origenerator")
        restore.assert_called_once_with(4242, activate=False)

    def test_a_restore_the_busy_app_dropped_is_retried_next_pass(self):
        """The app's boot blocks its main thread, so a restore can time out
        through the hung-window guard and do nothing.  The converger judges
        from the WINDOW each pass — still minimized means try again — instead
        of remembering it as shown and leaving a resumed session parked until
        the user digs it out of the taskbar."""
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        with patch.object(windows, "hwnd", return_value=4242), \
             patch("fun_time.role_windows.is_window_minimized", return_value=True), \
             patch("fun_time.role_windows.restore_window") as restore, \
             patch("fun_time.role_windows.set_always_on_top"):
            windows.converge_origenerator_window("nau", "origenerator")
            windows.converge_origenerator_window("nau", "origenerator")
        assert restore.call_count == 2

    def test_a_shown_window_out_of_the_band_is_re_promoted(self):
        # Restored but buried (the topmost bit never took): the converger
        # re-bands it rather than reading "not minimized" as converged.
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        with patch.object(windows, "hwnd", return_value=4242), \
             patch("fun_time.role_windows.is_window_minimized", return_value=False), \
             patch("fun_time.role_windows.is_window_topmost", return_value=False), \
             patch("fun_time.role_windows.restore_window") as restore, \
             patch("fun_time.role_windows.set_always_on_top") as promote:
            windows.converge_origenerator_window("nau", "origenerator")
        restore.assert_not_called()
        promote.assert_any_call(4242, True)

    def test_player_mode_parks_a_window_left_up(self):
        windows = make_windows(pids={"origenerator": HOSTED_PID})
        with patch.object(windows, "hwnd", return_value=4242), \
             patch("fun_time.role_windows.is_window_minimized", return_value=False), \
             patch("fun_time.role_windows.minimize_window") as minimize:
            windows.converge_origenerator_window("nau", "player")
        minimize.assert_called_once_with(4242, activate=False)

    def test_without_a_hosted_app_the_converger_is_inert(self):
        windows = make_windows()
        with patch.object(windows, "hwnd") as resolve:
            windows.converge_origenerator_window("nau", "origenerator")
        resolve.assert_not_called()
