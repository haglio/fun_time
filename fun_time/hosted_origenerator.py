from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path
from typing import NamedTuple

from app_support import origenerator_handoff as handoff
from app_support.win32 import process_started
from funestra.file_channel import append_command

from .fun_time_funestras import FunTimeFunestra
from .hosted_contract import (
    NO_HOSTED_VOCABULARY,
    HostedVocabulary,
    the_args_it_declares,
    what_it_answers_by_voice,
)
from .manifest import LaunchManifest
from .runtime_flow import write_flag_file
from .session_handoff import forget_the_kept_origenerator, kept_origenerator
from .standalone_origenerator import OpenOrigenerator, claim_the_osr2, the_open_origenerator
from .window_layout import WindowLayoutPlan, screen_layout
from .windows_bridge_startup import (
    HandedFunestra,
    OrigeneratorSession,
    launch_origenerator,
    origenerator_interpreter,
    origenerator_launch_kwargs,
    origenerator_session_args,
)

logger = logging.getLogger(__name__)

HAND_OVER = "HAND_OVER"
TAKE_BACK = "TAKE_BACK"

_NO_GRAB_WHERE_THE_WINDOW_ITSELF_SHOWS = (None, None)

ANSWER_BUDGET_S = 20.0
BOOTING_ANSWER_BUDGET_S = 90.0
_PUBLISH_POLL_S = 0.25


class HostedApp(NamedTuple):
    pid: int
    already_open: bool
    taken_over: bool
    slideshow_on: tuple[FunTimeFunestra, ...]


def _adopt_a_kept_origenerator(m: LaunchManifest) -> HostedApp | None:
    state_dir = Path(m.commands.origenerator_status_file).parent
    kept = kept_origenerator(state_dir)
    forget_the_kept_origenerator(state_dir)
    if kept is None or process_started(kept.pid) != kept.created_at:
        return None
    _lift_the_pause_an_earlier_omnipause_stranded(m)
    Path(m.commands.origenerator_cmd_file).write_text("", encoding="utf-8")
    logger.info("Adopted the hosted Origenerator left running (pid %d)", kept.pid)
    return HostedApp(kept.pid, already_open=True, taken_over=kept.taken_over,
                     slideshow_on=())


def _budget_for(open_app: OpenOrigenerator) -> float:
    return BOOTING_ANSWER_BUDGET_S if open_app.starting else ANSWER_BUDGET_S


def _it_proved_alive_by_reading_the_takeover(open_app: OpenOrigenerator) -> bool:
    give_up_at = time.monotonic() + _budget_for(open_app)
    while handoff.a_takeover_waits_in(open_app.place):
        if time.monotonic() >= give_up_at:
            return False
        time.sleep(_PUBLISH_POLL_S)
    return True


def _the_funestras_it_is_handed(m: LaunchManifest) -> dict[str, HandedFunestra]:
    return {
        funestra.label: HandedFunestra(
            playlist_file=m.satellite(funestra).playlist_file,
            cmd_file=m.satellite(funestra).cmd_file,
            status_file=m.satellite(funestra).status_file,
            hud_file=m.satellite(funestra).origenerator_hud_file,
        )
        for funestra in FunTimeFunestra.SATELLITES
    }


def the_session_it_is_handed(m: LaunchManifest, plan: WindowLayoutPlan, *,
                             in_a_headset: bool) -> OrigeneratorSession:
    frames_file, input_file = (
        (m.commands.origenerator_frames_file, m.commands.origenerator_input_file)
        if in_a_headset else _NO_GRAB_WHERE_THE_WINDOW_ITSELF_SHOWS)
    return OrigeneratorSession(
        layout_plan=plan,
        frames_file=frames_file,
        input_file=input_file,
        command_file=m.commands.origenerator_cmd_file,
        paused_file=m.commands.origenerator_paused_file,
        status_file=m.commands.origenerator_status_file,
        dashboard_cmd_file=m.commands.dashboard_cmd_file,
        funestras=_the_funestras_it_is_handed(m),
    )


def what_an_adopted_app_is_told(frames_file: str, input_file: str, *, in_a_headset: bool) -> str:
    return f"{HAND_OVER}|{frames_file}|{input_file}" if in_a_headset else TAKE_BACK


def _lift_the_pause_an_earlier_omnipause_stranded(m: LaunchManifest) -> None:
    write_flag_file(m.commands.origenerator_paused_file, False)


def _clear_last_sessions_huds(session: OrigeneratorSession) -> None:
    for funestra in session.funestras.values():
        Path(funestra.hud_file).unlink(missing_ok=True)


def _remove_unless_a_live_app_still_maps_it(name: str) -> None:
    with contextlib.suppress(OSError):
        Path(name).unlink(missing_ok=True)


def _clear_a_frame_and_presses_that_would_read_as_live(m: LaunchManifest) -> None:
    for name in (m.commands.origenerator_frames_file,
                 m.commands.origenerator_input_file):
        _remove_unless_a_live_app_still_maps_it(name)


def _the_monitor_layout_core_is_parked_in(m: LaunchManifest) -> WindowLayoutPlan:
    return screen_layout(m.layout, rfb_mode_selector=m.runtime.hosts_origenerator).plan


def bring_up_the_hosted_app(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan | None = None,
    sibling_checkouts: str,
    in_a_headset: bool = False,
) -> HostedApp | None:
    origenerator_dir = m.runtime.origenerator_dir.strip()
    if not origenerator_dir:
        return None
    if plan is None:
        plan = _the_monitor_layout_core_is_parked_in(m)
    claim_the_osr2(origenerator_dir)
    if in_a_headset:
        _clear_a_frame_and_presses_that_would_read_as_live(m)
    kept = _adopt_a_kept_origenerator(m)
    if kept is not None:
        crossing = what_an_adopted_app_is_told(m.commands.origenerator_frames_file,
                                               m.commands.origenerator_input_file,
                                               in_a_headset=in_a_headset)
        append_command(Path(m.commands.origenerator_cmd_file), crossing)
        logger.info("Told the adopted app which session this is: %s", crossing)
        return kept
    _lift_the_pause_an_earlier_omnipause_stranded(m)
    origenerator_cmd_file = Path(m.commands.origenerator_cmd_file)
    origenerator_cmd_file.parent.mkdir(parents=True, exist_ok=True)
    origenerator_cmd_file.write_text("", encoding="utf-8")
    Path(m.commands.origenerator_status_file).unlink(missing_ok=True)
    session = the_session_it_is_handed(m, plan, in_a_headset=in_a_headset)
    _clear_last_sessions_huds(session)
    open_app = the_open_origenerator(origenerator_dir)
    if open_app is not None:
        handoff.hand_over(open_app.place, pid=open_app.pid,
                          args=the_args_it_declares(open_app.checkout,
                                                    origenerator_session_args(session)))
        logger.info("Took over the Origenerator %s from %s (pid %d)",
                    "still starting" if open_app.starting else "already open",
                    open_app.checkout, open_app.pid)
        if _it_proved_alive_by_reading_the_takeover(open_app):
            return HostedApp(open_app.pid, already_open=not open_app.starting,
                             taken_over=True, slideshow_on=open_app.slideshow_on)
        logger.warning("The Origenerator we took over (pid %d) never read the takeover "
                       "in %.0fs; launching ours instead", open_app.pid,
                       _budget_for(open_app))
    origenerator_pid = launch_origenerator(
        python_exe=_its_interpreter(m, origenerator_dir),
        origenerator_dir=origenerator_dir,
        session=session,
        sibling_checkouts=sibling_checkouts,
    )
    logger.info("Origenerator launched from %s (pid %d)", origenerator_dir, origenerator_pid)
    return HostedApp(origenerator_pid, already_open=False, taken_over=False, slideshow_on=())


def _its_interpreter(m: LaunchManifest, origenerator_dir: str) -> str | Path:
    return (m.executables.origenerator_python_exe.strip()
            or _the_install_its_pinned_libraries_are_in(origenerator_dir))


def _the_install_its_pinned_libraries_are_in(origenerator_dir: str) -> Path:
    return origenerator_interpreter(origenerator_dir)


def what_it_answers_by_voice_in(m: LaunchManifest, **asking) -> HostedVocabulary:
    origenerator_dir = m.runtime.origenerator_dir.strip()
    if not origenerator_dir:
        return NO_HOSTED_VOCABULARY
    return what_it_answers_by_voice(
        origenerator_dir, python_exe=_its_interpreter(m, origenerator_dir),
        launch_kwargs=origenerator_launch_kwargs(
            origenerator_dir=origenerator_dir, sibling_checkouts=m.runtime.sibling_checkouts),
        **asking)
