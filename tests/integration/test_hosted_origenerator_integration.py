from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from funestra.modes import MainMode
from funestra.satellite_hud import parse_hud
from shared_ui.preview import Preview, window_title

from fun_time.event_log import event_log_path
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.fun_time_windows import find_origenerators_window
from fun_time.process_tree import kill_process_tree
from fun_time.rfb_modes import RfbMode
from fun_time.satellite_control import read_satellite_status
from fun_time.shared_state import (
    read_shared_state,
    shared_state_path,
    write_shared_state,
)
from fun_time.win32 import (
    describe_windows,
    is_window_minimized,
    is_window_topmost,
    iter_zorder,
    set_always_on_top,
    window_process,
    windows_obscuring,
)
from fun_time.window_names import ORIGENERATOR_TITLE
from fun_time.windows_bridge_orchestrator import (
    ORIGENERATOR_BOOT_BUDGET_S,
    _fix_post_loading_windows,
)
from fun_time.windows_bridge_sequencer import StartupResult
from fun_time.windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

from .integration_support import (
    COMMAND_BUDGET_S,
    QUIT_BUDGET_S,
    START_BUDGET_S,
    FunTimeIntegrationSession,
    await_window,
    build_integration_config,
    build_integration_temp_root,
    seen_within,
    wait_for,
)
from .stand_in_origenerator import (
    AN_INTERPRETER_WITH_TKINTER_AND_FUNESTRA,
    host_the_stand_in,
    write_stand_in_checkout,
)

pytestmark = [
    pytest.mark.real_startup_waits,
    pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
),
]


_FUNESTRAS = (FunTimeFunestra.PORTRAIT, FunTimeFunestra.LANDSCAPE)
_A_START_WITH_EVERY_CORE_BUSY_S = 2 * START_BUDGET_S
_A_START_THAT_WAITS_FOR_THE_APP_S = (
    _A_START_WITH_EVERY_CORE_BUSY_S + ORIGENERATOR_BOOT_BUDGET_S)
_TWO_SESSIONS_AND_EVERY_WAIT_THEY_MAKE_S = (
    _A_START_WITH_EVERY_CORE_BUSY_S + _A_START_THAT_WAITS_FOR_THE_APP_S
    + 2 * COMMAND_BUDGET_S)
_A_SESSION_A_QUIT_AND_FIVE_WAITS_S = (
    _A_START_WITH_EVERY_CORE_BUSY_S + QUIT_BUDGET_S + 5 * COMMAND_BUDGET_S)
_THAT_AND_THE_SHARED_SESSIONS_QUIT_S = _A_SESSION_A_QUIT_AND_FIVE_WAITS_S + QUIT_BUDGET_S
_FUNESTRA_TITLES = (SATELLITE_PORTRAIT_TITLE, SATELLITE_LANDSCAPE_TITLE)
_A_BURIAL_ARRIVES_SECONDS_LATE_S = 10.0


def _host_stub(config_path: Path, stub_root: Path) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    host_the_stand_in(raw, stub_root)
    config_path.write_text(json.dumps(raw), encoding="utf-8")


def _open_by_path_where_a_start_reaps_every_dash_m(stub_root: Path, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [AN_INTERPRETER_WITH_TKINTER_AND_FUNESTRA,
         str(stub_root / "origenerator" / "__main__.py"), *args],
        cwd=str(stub_root))


def _parked_core(pid: int) -> int:
    hwnd = find_origenerators_window(pid)
    return hwnd if hwnd and is_window_minimized(hwnd) else 0


@pytest.fixture(scope="module")
def the_modules_hosted_session():
    temp_root = build_integration_temp_root()
    stub_root = write_stand_in_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        pid = session.read_child_pids().get("origenerator_pid", 0)
        assert pid, "the session did not record a hosted origenerator child"
        hwnd = seen_within(lambda: _parked_core(pid), seconds=20.0)
        stderr_file = session.config.paths.state_dir / "orchestrator_stderr.log"
        stderr_tail = stderr_file.read_text(encoding="utf-8", errors="replace")[-2000:] \
            if stderr_file.exists() else "(no stderr file)"
        assert hwnd, f"the stub's parked main window never appeared; stderr: {stderr_tail}"
        yield session, hwnd
    finally:
        session.stop()


def _playing(session, funestra: FunTimeFunestra) -> str:
    return read_satellite_status(session.config.satellite(funestra).status_file).video


def _shows_the_stub(session, funestra: FunTimeFunestra) -> bool:
    return Path(_playing(session, funestra)).name == f"{funestra.label}.png"


def _wait_for_the_slideshows(session) -> None:
    for funestra in _FUNESTRAS:
        wait_for(lambda funestra=funestra: _shows_the_stub(session, funestra),
                 desc=f"the {funestra.label} Funestra to show the hosted app's slideshow")


def _press_once_the_app_booting_behind_fun_time_answers(session, *commands: str) -> Path:
    state_file = shared_state_path(session.config.paths.state_dir)
    wait_for(lambda: read_shared_state(state_file).origenerator_ready,
             timeout=START_BUDGET_S, desc="the hosted app to publish a status")
    for command in commands:
        session.write_dashboard_command(command)
    return state_file


def _put_origenerator_in_the_browsers_place(session) -> None:
    state_file = _press_once_the_app_booting_behind_fun_time_answers(session, "origenerator_activate")
    wait_for(lambda: read_shared_state(state_file).rfb_mode is RfbMode.ORIGENERATOR,
             desc="Origenerator to take the Random Faves Browser's place")


def _the_omnipause_cycle_the_demo_failed_after(session) -> None:
    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_log("OmniPause: entering")
    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_log("OmniPause: leaving")


def test_the_strip_raises_the_parked_window_and_the_browsers_button_parks_it(the_modules_hosted_session):
    session, hwnd = the_modules_hosted_session
    assert is_window_minimized(hwnd)

    _the_omnipause_cycle_the_demo_failed_after(session)

    _put_origenerator_in_the_browsers_place(session)
    wait_for(lambda: not is_window_minimized(hwnd),
             desc="the hosted window to be restored")
    wait_for(lambda: is_window_topmost(hwnd),
             desc="the hosted window to join the topmost band")

    session.write_dashboard_command("rfb_activate")
    session.wait_for_new_log("Random Faves Browser mode")
    wait_for(lambda: is_window_minimized(hwnd),
             desc="the hosted window to park again")


def test_the_funestras_play_the_hosted_apps_slideshows_from_the_start(the_modules_hosted_session):
    session, _hwnd = the_modules_hosted_session

    _wait_for_the_slideshows(session)


def test_the_browsers_place_given_to_origenerator_while_paused_bands_the_window_on_resume(
        the_modules_hosted_session):
    session, hwnd = the_modules_hosted_session
    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_new_log("OmniPause: entering")

    _put_origenerator_in_the_browsers_place(session)
    _wait_for_the_slideshows(session)

    session.write_dashboard_command("omnipause_toggle")
    session.wait_for_new_log("OmniPause: leaving")
    wait_for(lambda: is_window_topmost(hwnd),
             desc="the hosted window to join the topmost band once the pause lifts")

    session.write_dashboard_command("rfb_activate")
    wait_for(lambda: is_window_minimized(hwnd), desc="the hosted window to park again")


def _input_method_windows_of(processes: set[int]) -> list[int]:
    user32 = ctypes.WinDLL("user32")
    found = []

    @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def look_at(hwnd, _):
        class_name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, class_name, 64)
        if class_name.value in ("IME", "MSCTFIME UI") and window_process(hwnd) in processes:
            found.append(hwnd)
        return True

    user32.EnumWindows(look_at, 0)
    return found


def test_no_funestra_keeps_input_method_windows_that_could_stall_its_band(the_modules_hosted_session):
    funestras = {window_process(await_window(title, 10, exact=True))
                 for title in ("Main Funestra", SATELLITE_PORTRAIT_TITLE, SATELLITE_LANDSCAPE_TITLE)}

    assert 0 not in funestras
    assert _input_method_windows_of(funestras) == []


def test_the_post_overlay_pass_rebands_satellites_recorded_under_shim_pids(the_modules_hosted_session):
    session, _hwnd = the_modules_hosted_session
    pids = session.read_child_pids()
    portrait = await_window("Portrait AI Funestra", 10, exact=True)
    landscape = await_window("Landscape AI Funestra", 10, exact=True)
    assert portrait
    assert landscape

    set_always_on_top(portrait, False)
    set_always_on_top(landscape, False)
    for funestra, hwnd in (("portrait", portrait), ("landscape", landscape)):
        wait_for(lambda hwnd=hwnd: not is_window_topmost(hwnd),
                 desc=f"the {funestra} Funestra to leave the topmost band")

    _fix_post_loading_windows(StartupResult(
        main_funestra_pid=pids["main_funestra_pid"],
        portrait_pid=pids["portrait_pid"],
        landscape_pid=pids["landscape_pid"],
        dashboard_pid=0,
        audio_pid=0,
        main_mode=MainMode.KINO,
    ))

    for funestra, hwnd in (("portrait", portrait), ("landscape", landscape)):
        wait_for(lambda hwnd=hwnd: is_window_topmost(hwnd),
                 desc=f"the {funestra} Funestra to be put back in the topmost band")


@pytest.mark.timeout(_A_SESSION_A_QUIT_AND_FIVE_WAITS_S)
def test_a_real_session_opening_in_the_hosted_app_leaves_its_slideshows_on_top():
    temp_root = build_integration_temp_root()
    stub_root = write_stand_in_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    session = FunTimeIntegrationSession(config_path)
    overlay_env = {
        "FUN_TIME_INTEGRATION_OVERLAYS": "1",
        "FUN_TIME_DISABLE_DASHBOARD": "0",
    }
    try:
        session.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S, env_overrides=overlay_env)
        events = event_log_path(session.config.paths.state_dir)
        text = events.read_text(encoding="utf-8", errors="replace") if events.exists() else ""
        assert "Loading screen launched" in text, (
            "the session did not take the loading-screen path, which is where "
            "the reveal and the settle pass live"
        )

        _wait_for_the_slideshows(session)
        for funestra in _FUNESTRAS:
            hud_file = session.config.satellite(funestra).hud_file
            worn = wait_for(
                lambda hud_file=hud_file, funestra=funestra: _hud_of(hud_file, funestra),
                desc=f"the {funestra.label} Funestra to wear the hosted app's HUD")
            assert worn.rows[0][0].command == f"{funestra.label}_minimize"

        funestras = {title: await_window(title, 10, exact=True) for title in _FUNESTRA_TITLES}
        each_other = set(funestras.values())
        burial = seen_within(lambda: _first_burial(funestras, each_other),
                             seconds=_A_BURIAL_ARRIVES_SECONDS_LATE_S, every_s=0.5)
        assert not burial, burial
    finally:
        session.stop()


def _first_burial(
        funestras: dict[str, int], overlapping_on_the_small_faked_monitors: set[int]) -> str:
    stack = iter_zorder()
    for title, hwnd in funestras.items():
        if covering := [w for w in windows_obscuring(hwnd, stack)
                        if w.hwnd not in overlapping_on_the_small_faked_monitors]:
            return f"{title} (hwnd={hwnd}) was covered after the switch by: {describe_windows(covering)}"
    return ""


def _hud_of(hud_file: Path, funestra: FunTimeFunestra):
    if not hud_file.exists():
        return None
    hud = parse_hud(hud_file.read_text(encoding="utf-8"))
    if hud is None or hud.title != f"Stub {funestra.label} slideshow":
        return None
    return hud


def _told_the_stub(session) -> str:
    log = session.config.paths.state_dir / "origenerator_stub.log"
    return log.read_text(encoding="utf-8").upper() if log.exists() else ""


@pytest.mark.timeout(_A_SESSION_A_QUIT_AND_FIVE_WAITS_S)
def test_a_trash_press_on_a_satellite_is_handed_to_the_hosted_app():
    portrait = FunTimeFunestra.PORTRAIT
    temp_root = build_integration_temp_root()
    stub_root = write_stand_in_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S)
        wait_for(lambda: _shows_the_stub(session, portrait),
                 desc="the Portrait Funestra to show the hosted app's slideshow")

        session.write_dashboard_command("portrait_trash")

        wait_for(lambda: "TOLD PORTRAIT_TRASH" in _told_the_stub(session),
                 desc="the hosted app to be told of the trash press")
        assert not any(session.trash_dir.iterdir())
    finally:
        session.stop()


def _the_state_beside_the_playlists_a_real_session_leaves(config_path: Path) -> Path:
    first = FunTimeIntegrationSession(config_path)
    try:
        first.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S)
    finally:
        first.end_as_a_crash_does()
    return shared_state_path(first.config.paths.state_dir)


@pytest.mark.timeout(_TWO_SESSIONS_AND_EVERY_WAIT_THEY_MAKE_S)
def test_an_app_left_with_origenerator_in_the_browsers_place_opens_with_it_there():
    temp_root = build_integration_temp_root()
    stub_root = write_stand_in_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)

    state_file = _the_state_beside_the_playlists_a_real_session_leaves(config_path)
    write_shared_state(state_file, replace(
        read_shared_state(state_file), rfb_mode=RfbMode.ORIGENERATOR, max_intensity=38))

    second = FunTimeIntegrationSession(config_path)
    try:
        second.start(wait_seconds=_A_START_THAT_WAITS_FOR_THE_APP_S)
        _wait_for_the_slideshows(second)
        opened_in = read_shared_state(state_file)
        assert opened_in.rfb_mode is RfbMode.ORIGENERATOR
        assert opened_in.max_intensity == 38
    finally:
        second.stop()


@pytest.mark.timeout(_A_SESSION_A_QUIT_AND_FIVE_WAITS_S)
def test_an_origenerator_taken_over_while_showing_a_slideshow_takes_the_browsers_place_too():
    temp_root = build_integration_temp_root()
    stub_root = write_stand_in_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    open_app = _open_by_path_where_a_start_reaps_every_dash_m(
        stub_root, "--showing", FunTimeFunestra.PORTRAIT.label)
    session = FunTimeIntegrationSession(config_path)
    try:
        wait_for((stub_root / "state" / "fun_time_showing.txt").exists,
                 desc="the open app to say it is showing a slideshow")
        session.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S)

        _wait_for_the_slideshows(session)
        state = read_shared_state(shared_state_path(session.config.paths.state_dir))
        assert state.rfb_mode is RfbMode.ORIGENERATOR
    finally:
        session.stop()
        kill_process_tree(open_app.pid)


def _wait_for_windows_to_restore_and_unband_it_in_its_own_time(hwnd: int) -> None:
    wait_for(lambda: not is_window_minimized(hwnd),
             desc="the handed-back window to stand open again")
    wait_for(lambda: not is_window_topmost(hwnd),
             desc="the handed-back window to leave the topmost band")


@pytest.mark.timeout(_THAT_AND_THE_SHARED_SESSIONS_QUIT_S)
def test_an_origenerator_already_open_is_taken_into_the_session_rather_than_doubled():
    temp_root = build_integration_temp_root()
    stub_root = write_stand_in_checkout(temp_root / "origenerator_stub")
    config_path = build_integration_config(temp_root)
    _host_stub(config_path, stub_root)
    open_app = _open_by_path_where_a_start_reaps_every_dash_m(
        stub_root, "--caption",
        window_title(ORIGENERATOR_TITLE, Preview(feature="a fabricated feature")))
    session = FunTimeIntegrationSession(config_path)
    try:
        offer = stub_root / "state" / "fun_time_offer.txt"
        offered_pid = int(wait_for(lambda: offer.exists() and offer.read_text(
            encoding="utf-8").split(), desc="the open app to offer itself")[0])
        session.start(wait_seconds=_A_START_WITH_EVERY_CORE_BUSY_S)

        assert session.read_child_pids().get("origenerator_pid") == offered_pid
        state_file = shared_state_path(session.config.paths.state_dir)
        wait_for(lambda: read_shared_state(state_file).origenerator_ready, timeout=5,
                 desc="the mode to open without waiting to hear from the app")
        hwnd = wait_for(lambda: _parked_core(offered_pid),
                        desc="the open app's window to be parked by the takeover")
        session.write_dashboard_command("origenerator_activate")
        session.wait_for_log("Origenerator mode")
        wait_for(lambda: not is_window_minimized(hwnd),
                 desc="the taken-over window to be restored")
        wait_for(lambda: is_window_topmost(hwnd),
                 desc="the taken-over window to join the topmost band")

        session.quit_gracefully()

        wait_for(offer.exists, desc="the handed-back app to offer itself again")
        assert open_app.poll() is None, "the session closed the app it was meant to hand back"
        _wait_for_windows_to_restore_and_unband_it_in_its_own_time(hwnd)
    finally:
        session.stop()
        kill_process_tree(open_app.pid)
