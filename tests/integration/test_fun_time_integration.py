from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

from fun_time.media_actions import remove_from_favs
from fun_time.satellite_control import read_satellite_status
from fun_time.windows_bridge_sequencer import _resolve_satellite_hwnds
from fun_time.win32 import (
    find_window_by_pid,
    find_window_by_title,
    is_process_alive,
    is_window_minimized,
    is_window_topmost,
)

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)


@pytest.fixture(scope="module")
def shared_integration_session():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        yield session
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


@pytest.fixture
def isolated_integration_session():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        yield session
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


def test_fun_time_startup_runtime_smoke(shared_integration_session: FunTimeIntegrationSession):
    assert shared_integration_session.windows_bridge_log.exists()
    assert shared_integration_session.orchestrator_log.exists()


def test_fun_time_portrait_lock_unlock_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait satellite", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Unlocked portrait satellite", timeout=12)


def test_fun_time_omnipause_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: entering", timeout=12)

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: leaving", timeout=12)


def test_fun_time_fmode_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode hotkey: enabled", timeout=12)

    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode hotkey: disabled", timeout=12)


def test_fun_time_genau_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    """Pressing 'g' (genau_activate) then 'n' (nau_activate) switches modes."""
    s = shared_integration_session
    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode", timeout=12)

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Genau paused file to flip off (active)",
    )
    s.wait_until(
        lambda: s.config.nau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Nau paused file to flip on (inactive)",
    )

    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip back on (inactive)",
    )
    s.wait_until(
        lambda: s.config.nau_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Nau paused file to flip back off (active)",
    )


def test_fun_time_mode_switch_swaps_primary_slot_window_visibility(shared_integration_session: FunTimeIntegrationSession):
    """The primary-slot players share one screen rect, so a mode switch swaps
    which is on screen: the active mode's player is restored, the idle one
    minimized (never hidden — both keep a taskbar button all session, so both
    stay findable by title; is_window_minimized tells them apart). Nau floats
    topmost whenever it owns the display (nau and hybrid), so its video is above
    the desktop; in hybrid Genau's HUD is topmost too, stacked above Nau."""
    s = shared_integration_session

    # nau mode: Nau restored AND topmost (it owns the whole display), Genau
    # minimized.  The lookup is exact because 'Nau' is a substring of 'Genau'.
    s.wait_until(
        lambda: find_window_by_title("Nau", exact=True) != 0,
        timeout=12,
        description="Nau window to exist in nau mode",
    )
    nau_hwnd = find_window_by_title("Nau", exact=True)
    s.wait_until(
        lambda: is_window_topmost(nau_hwnd) and not is_window_minimized(nau_hwnd),
        timeout=5,
        description="Nau to be restored and topmost in nau mode",
    )
    s.wait_until(
        lambda: is_window_minimized(find_window_by_title("Genau")),
        timeout=12,
        description="Genau window to be minimized in nau mode",
    )

    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode", timeout=12)

    s.wait_until(
        lambda: is_window_minimized(find_window_by_title("Nau", exact=True)),
        timeout=12,
        description="Nau window to minimize when Genau mode activates",
    )
    s.wait_until(
        lambda: not is_window_minimized(find_window_by_title("Genau")),
        timeout=12,
        description="Genau window to restore in genau mode",
    )

    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)

    s.wait_until(
        lambda: not is_window_minimized(find_window_by_title("Nau", exact=True)),
        timeout=12,
        description="Nau window to restore in nau mode",
    )
    s.wait_until(
        lambda: is_window_minimized(find_window_by_title("Genau")),
        timeout=12,
        description="Genau window to minimize again in nau mode",
    )

    # hybrid mode: Nau stays restored and topmost (video above the desktop) with
    # Genau's HUD promoted above it — BOTH in the topmost band.  This is the case
    # the nau-mode float must extend to, not break.
    s.write_dashboard_command("hybrid_activate")
    s.wait_for_new_log("Switched to hybrid mode", timeout=12)
    s.wait_until(
        lambda: not is_window_minimized(find_window_by_title("Nau", exact=True)),
        timeout=12,
        description="Nau window to stay restored in hybrid mode",
    )
    s.wait_until(
        lambda: is_window_topmost(find_window_by_title("Nau", exact=True)),
        timeout=5,
        description="Nau to float topmost in hybrid (video above the desktop)",
    )
    s.wait_until(
        lambda: is_window_topmost(find_window_by_title("Genau")),
        timeout=5,
        description="Genau's HUD to be topmost in hybrid, stacked above Nau",
    )

    # Back to nau mode: Nau reclaims the topmost band, leaving the session where
    # it started.
    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)
    s.wait_until(
        lambda: is_window_topmost(find_window_by_title("Nau", exact=True)),
        timeout=5,
        description="Nau to reclaim the topmost band back in nau mode",
    )


def test_fun_time_landscape_lock_unlock_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape satellite", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Unlocked landscape satellite", timeout=12)


def test_fun_time_portrait_next_cancels_lock(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait satellite", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_next")
    time.sleep(1.5)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait satellite", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Unlocked portrait satellite", timeout=12)


def test_fun_time_landscape_next_cancels_lock(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape satellite", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_next")
    time.sleep(1.5)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape satellite", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Unlocked landscape satellite", timeout=12)


def test_fun_time_omnipause_while_genau_mode(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("genau_activate")
    shared_integration_session.wait_for_new_log("Switched to genau mode", timeout=12)

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: entering", timeout=12)
    shared_integration_session.wait_until(
        lambda: shared_integration_session.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip on",
    )

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: leaving", timeout=12)
    shared_integration_session.wait_until(
        lambda: shared_integration_session.config.genau_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Genau paused file to flip off",
    )

    shared_integration_session.write_dashboard_command("nau_activate")
    shared_integration_session.wait_for_new_log("Switched to nau mode", timeout=12)


def test_fun_time_omnipause_does_not_kill_genau(shared_integration_session: FunTimeIntegrationSession):
    """Regression: omnipause must pause Genau, not close it.

    The old AHK HandleOmniPauseToggle never removed Genau's topmost
    flag.  When omnipause was ported to Python, an explicit
    set_topmost(Genau, False) was added by mistake, causing the
    window to fall behind other windows (appearing "closed").  Verify the
    Genau process survives an omnipause round-trip while in genau
    mode.
    """
    s = shared_integration_session
    rh_pid = s.read_genau_pid()
    assert is_process_alive(rh_pid), "Genau should be alive before test"

    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode", timeout=12)

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: entering", timeout=12)
    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip on",
    )

    # Genau must still be running — omnipause should pause, not close.
    assert is_process_alive(rh_pid), (
        "Genau process died during omnipause — "
        "Esc should pause Genau, not close it"
    )

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: leaving", timeout=12)

    assert is_process_alive(rh_pid), "Genau should survive leaving omnipause"

    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)


def test_fun_time_omnipause_drops_satellites_from_topmost(shared_integration_session: FunTimeIntegrationSession):
    """Entering OmniPause must free the desktop — the Portrait and Landscape
    satellites must leave the topmost band, not stay pinned on top of the
    windows the user reaches for while paused."""
    s = shared_integration_session
    # Known starting point: nau mode, not omnipaused ("play" is an idempotent
    # leave-omnipause; a no-op when already live).
    s.write_dashboard_command("nau_activate")
    s.write_dashboard_command("play")

    # Resolve exactly as startup does: by each satellite's DISTINCT caption
    # ("Portrait AI Player" / "Landscape AI Player").  A pid cannot find either
    # window — the venv's pythonw launcher spawns the interpreter that owns it as
    # a child — and the distinct captions are what tell portrait from landscape
    # without swapping them.
    portrait_hwnd, landscape_hwnd = _resolve_satellite_hwnds()
    assert portrait_hwnd, "Portrait satellite window must be resolvable"
    assert landscape_hwnd, "Landscape satellite window must be resolvable"

    # Satellites float topmost while the desktop is live.
    s.wait_until(
        lambda: is_window_topmost(portrait_hwnd) and is_window_topmost(landscape_hwnd),
        timeout=8,
        description="Portrait + Landscape satellites to be topmost before OmniPause",
    )

    # Enter OmniPause — every managed window must drop out of the topmost band.
    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: entering", timeout=12)
    s.wait_until(
        lambda: not is_window_topmost(portrait_hwnd),
        timeout=8,
        description="Portrait satellite to leave the topmost band on OmniPause enter",
    )
    s.wait_until(
        lambda: not is_window_topmost(landscape_hwnd),
        timeout=8,
        description="Landscape satellite to leave the topmost band on OmniPause enter",
    )

    # Restore the shared session.
    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: leaving", timeout=12)


def test_fun_time_omnipause_freezes_the_satellites(
    shared_integration_session: FunTimeIntegrationSession,
):
    """OmniPause freezes the native satellites through their paused flag file.

    The player obeys that flag every tick and simply cannot auto-advance while it
    is set, so entering OmniPause is a settled state that needs no policing.  We
    assert each satellite reports paused and its playhead stops moving."""
    s = shared_integration_session
    portrait_status = s.config.paths.state_dir / "portrait_status.txt"
    landscape_status = s.config.paths.state_dir / "landscape_status.txt"

    # Known starting point (nau mode, live): the satellites are playing.
    s.write_dashboard_command("nau_activate")
    s.write_dashboard_command("play")  # idempotent leave-omnipause; a no-op if live
    s.wait_until(
        lambda: not read_satellite_status(portrait_status).paused,
        timeout=10,
        description="Portrait satellite to be playing before OmniPause",
    )

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: entering", timeout=12)
    s.wait_until(
        lambda: read_satellite_status(portrait_status).paused,
        timeout=10,
        description="Portrait satellite to report paused under OmniPause",
    )
    s.wait_until(
        lambda: read_satellite_status(landscape_status).paused,
        timeout=10,
        description="Landscape satellite to report paused under OmniPause",
    )
    # The playhead must not advance while paused.
    pos_a = read_satellite_status(portrait_status).position_ms
    time.sleep(1.2)
    pos_b = read_satellite_status(portrait_status).position_ms
    assert pos_b == pos_a, f"paused satellite kept playing ({pos_a} -> {pos_b})"

    # Restore the shared session.
    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: leaving", timeout=12)


def test_fun_time_nau_nudge_seeks_playback(shared_integration_session: FunTimeIntegrationSession):
    """primary_nudge_next/prev in nau mode drive Nau's seek via its command
    file, observed through Nau's published status position."""
    s = shared_integration_session

    # Let the orchestrator finish processing commands from prior tests.
    time.sleep(2.0)
    # Ensure we're in nau mode so Nau is the active display and its seek is
    # observable in the published status.
    s.write_dashboard_command("nau_activate")
    # Wait for a *loaded* video: a non-zero duration means mpv knows the
    # length, so a seek target won't be clamped to 0 by an as-yet-unknown
    # duration (which would make the forward seek a no-op).
    s.wait_until(
        lambda: s.read_nau_status().video != "" and s.read_nau_duration_ms() > 0,
        timeout=15,
        description="Nau to report a loaded video with a known duration",
    )

    # The library is a random sample of real clips with mixed lengths, and a
    # ±10s nudge is only observable on a video long enough to hold ~15s of
    # forward headroom. Advance through the playlist until one loads that is
    # long enough for the seek assertions below.
    MIN_DURATION_MS = 25_000
    for _ in range(12):
        if s.read_nau_duration_ms() >= MIN_DURATION_MS:
            break
        prev_video = s.read_nau_status().video
        s.write_dashboard_command("primary_next")
        s.wait_until(
            lambda pv=prev_video: (
                s.read_nau_status().video not in ("", pv)
                and s.read_nau_duration_ms() > 0
            ),
            timeout=15,
            description="Nau to load the next video",
        )
    duration = s.read_nau_duration_ms()
    assert duration >= MIN_DURATION_MS, (
        f"no sampled video long enough for a ±10s nudge test: duration={duration}"
    )

    # The looping playhead sits at an arbitrary spot; if it is near the end, a
    # forward seek clamps at the duration and never advances. Nudge back until
    # there is comfortable forward headroom first.
    for _ in range(30):
        if s.read_nau_status().position_ms <= duration - 15_000:
            break
        s.write_dashboard_command("primary_nudge_prev")
        time.sleep(0.4)

    before = s.read_nau_status().position_ms
    assert before <= duration - 12_000, (
        f"could not create forward headroom: pos={before} duration={duration}"
    )

    s.write_dashboard_command("primary_nudge_next")
    s.wait_until(
        lambda: s.read_nau_status().position_ms >= before + 9_000,
        timeout=10,
        description=f"Nau to jump forward ~10s after nudge (before={before}, duration={duration})",
    )

    after_fwd = s.read_nau_status().position_ms
    s.write_dashboard_command("primary_nudge_prev")
    s.wait_until(
        lambda: s.read_nau_status().position_ms <= after_fwd - 9_000,
        timeout=10,
        description=f"Nau to jump back ~10s after nudge (after_fwd={after_fwd})",
    )


def test_fun_time_nau_record_loop_cancel_cycle(shared_integration_session: FunTimeIntegrationSession):
    """The record gesture round-trips through Nau: record → looping → cancel,
    observed through Nau's published loop state."""
    s = shared_integration_session
    s.wait_until(
        lambda: s.read_nau_status().video != "",
        timeout=15,
        description="Nau status file to report a current video",
    )
    assert s.read_nau_status().state == "normal"

    s.write_dashboard_command("nau_record_tap")
    s.wait_until(
        lambda: s.read_nau_status().state == "recording",
        timeout=10,
        description="Nau to enter recording state",
    )

    s.write_dashboard_command("nau_record_tap")
    s.wait_until(
        lambda: s.read_nau_status().state == "looping",
        timeout=10,
        description="Nau to enter looping state",
    )

    s.write_dashboard_command("nau_loop_cancel")
    s.wait_until(
        lambda: s.read_nau_status().state == "normal",
        timeout=10,
        description="Nau to return to normal state",
    )


def test_fun_time_hybrid_keeps_nau_as_the_display(shared_integration_session: FunTimeIntegrationSession):
    """Hybrid keeps Nau as the on-screen player while Genau drives the OSR2, so
    the video Nau was playing simply continues — nothing is handed to another
    player — and prev/next/nudge dispatch to Nau just as they do in nau mode.

    (The precise +10s Nau seek is covered by the nau-mode nudge test above,
    which exercises the identical dispatch path.)

    Must run before isolated-session tests (trash), whose teardown kills all
    recent player processes and would leave the shared session's players dead.
    """
    s = shared_integration_session

    nau_video_before = s.read_nau_status().video
    assert nau_video_before, "expected Nau to be playing before switching to hybrid"

    s.write_dashboard_command("hybrid_activate")
    s.wait_for_new_log("Switched to hybrid mode", timeout=12)

    # Nau stays the display and keeps playing its current video — no handoff.
    s.wait_until(
        lambda: s.read_nau_status().video == nau_video_before,
        timeout=12,
        description="Nau to keep playing its current video in hybrid mode",
    )

    # A nudge in hybrid reaches the normal dispatch path.
    s.write_dashboard_command("primary_nudge_next")
    s.wait_for_new_log("Dispatching command: primary_nudge_next", timeout=10)

    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)


def test_fun_time_landscape_trash_of_a_favorite_only_unfavorites_it(
    isolated_integration_session: FunTimeIntegrationSession,
):
    """Every sample the isolated session links in is seeded into its favs.csv, so
    the clip on screen is a favorite and discard demotes it: the row leaves the
    list, the file stays in the library, and the clip stays in the rotation —
    W then A comes straight back to it."""
    status_file = isolated_integration_session.config.paths.state_dir / "landscape_status.txt"
    isolated_integration_session.write_dashboard_command("landscape_trash")
    chunk = isolated_integration_session.wait_for_new_log("Removed from favorites on player 3:", timeout=12)
    match = re.search(r"Removed from favorites on player 3:\s*(.+)", chunk)
    assert match, "Expected the unfavorite log chunk to include the landscape path"
    demoted_path = Path(match.group(1).strip()).resolve()

    isolated_integration_session.wait_until(
        lambda: not isolated_integration_session.favs_contains(demoted_path),
        timeout=12,
        description="landscape sample to be removed from integration favs.csv",
    )
    assert demoted_path.exists(), "A demoted favorite must stay where it is"
    assert not any(p.name == demoted_path.name for p in isolated_integration_session.weird_dir.iterdir())

    isolated_integration_session.wait_until(
        lambda: Path(read_satellite_status(status_file).video or "x").resolve() != demoted_path,
        timeout=12,
        description="landscape satellite to advance off the demoted clip",
    )
    isolated_integration_session.write_dashboard_command("landscape_prev")
    isolated_integration_session.wait_until(
        lambda: Path(read_satellite_status(status_file).video or "x").resolve() == demoted_path,
        timeout=12,
        description="landscape prev to land back on the demoted clip, still in the rotation",
    )


def test_fun_time_portrait_trash_of_a_non_favorite_moves_it_to_weird(
    isolated_integration_session: FunTimeIntegrationSession,
):
    """Discarding a clip that is not in the favorites is the full condemnation —
    it leaves the playlist and the file moves into the weird dir."""
    status_file = isolated_integration_session.config.paths.state_dir / "portrait_status.txt"
    isolated_integration_session.wait_until(
        lambda: bool(read_satellite_status(status_file).video),
        timeout=12,
        description="portrait satellite to publish the clip it is playing",
    )
    # Take the clip out of the favorites the way the app does, so the discard
    # below meets an ordinary library file rather than a favorite.
    remove_from_favs(isolated_integration_session.favs_file, read_satellite_status(status_file).video)

    isolated_integration_session.write_dashboard_command("portrait_trash")
    chunk = isolated_integration_session.wait_for_new_log("Discarding from player 2:", timeout=12)
    match = re.search(r"Discarding from player 2:\s*(.+)", chunk)
    assert match, "Expected discard log chunk to include the discarded portrait path"
    trashed_path = Path(match.group(1).strip()).resolve()

    isolated_integration_session.wait_until(
        lambda: not isolated_integration_session.favs_contains(trashed_path),
        timeout=12,
        description="portrait sample to be removed from integration favs.csv",
    )
    isolated_integration_session.wait_until(
        lambda: any(p.name == trashed_path.name for p in isolated_integration_session.weird_dir.iterdir()),
        timeout=12,
        description="portrait sample to be moved into the integration weird dir",
    )


def _videos(playlist: Path) -> list[str]:
    """The video column of each playlist line, dropping any funscript."""
    return [
        line.partition("\t")[0]
        for line in playlist.read_text(encoding="utf-8").splitlines()
    ]


def test_fun_time_reopens_on_the_video_it_was_closed_on():
    """Close Fun Time on one video and it comes back on that one.

    Nothing is written at shutdown: each player publishes what it is showing to
    its status file every tick, and the next start rotates that player's playlist
    onto the video named there rather than building a fresh shuffle.  Only a real
    session proves it — the record has to survive the force-kill that ends one.

    Nau carries the assertion because its library is the several-entry one, so
    the resumed playlist has to be an exact rotation of the last one — an order
    a rebuild would reproduce only by chance.
    """
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)

    first = FunTimeIntegrationSession(config_path)
    playlist = first.config.nau_playlist_file
    try:
        first.start()
        opened_with = _videos(playlist)
        # Navigate off the top of the playlist, so resuming onto entry 0 — which
        # every session does anyway — cannot pass this by accident.
        first.write_dashboard_command("primary_next")
        first.wait_until(
            lambda: first.read_nau_status().video not in ("", opened_with[0]),
            timeout=20,
            description="Nau to navigate off the first video",
        )
        # Then freeze the session before closing it. Some of the primary library
        # is seconds long, and a Nau that auto-advanced while the shutdown ran
        # would leave behind a different video than the one read here.
        first.write_dashboard_command("omnipause_toggle")
        first.wait_until(
            lambda: first.read_nau_status().paused,
            timeout=20,
            description="Nau to freeze under OmniPause",
        )
        left_on = first.read_nau_status().video
        assert left_on != opened_with[0], "the session must close off the top of its playlist"
        first.quit_gracefully(timeout=15.0)
    finally:
        first.stop()

    second = FunTimeIntegrationSession(config_path)
    try:
        second.start()
        resumed = _videos(playlist)
        position = opened_with.index(left_on)
        assert resumed == opened_with[position:] + opened_with[:position], (
            "the reopened playlist must be last session's rotated onto the video it "
            f"was closed on (entry {position}), not a fresh shuffle\n"
            f"left on: {left_on}\n"
            "opened with:\n" + "\n".join(opened_with)
            + "\nreopened with:\n" + "\n".join(resumed)
        )
        second.wait_until(
            lambda: second.read_nau_status().video == left_on,
            timeout=20,
            description="Nau to come back up on the video the last session ended on",
        )
    finally:
        second.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


def test_fun_time_quit_cleans_up_processes():
    """The real quit path (AHK exit → orchestrator cleanup) must kill all child processes."""
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()

        child_pids = session.read_child_pids()
        live_pids = {name: pid for name, pid in child_pids.items() if pid and is_process_alive(pid)}
        assert live_pids, "Expected at least some child processes to be running after startup"

        session.quit_gracefully(timeout=15.0)

        assert session._proc.poll() is not None, "Orchestrator should have exited"

        deadline = time.time() + 5.0
        while time.time() < deadline:
            still_alive = {name: pid for name, pid in live_pids.items() if is_process_alive(pid)}
            if not still_alive:
                break
            time.sleep(0.5)
        assert not still_alive, (
            f"Quit path failed to clean up processes: {still_alive}\n{session._log_tail()}"
        )
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)

