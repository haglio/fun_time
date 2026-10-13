"""The hosted Origenerator, brought up the same way by either shape of session.

Its shows are the Satellite Funestras' own playlists (:mod:`fun_time.funestra_handover`),
and the headset's satellites are Funestras, so a VR session hosts it as a desktop
session does.
"""
from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path
from typing import NamedTuple

from funestra.file_channel import append_command

from .hosted_contract import (
    NO_HOSTED_VOCABULARY,
    HostedVocabulary,
    the_args_it_declares,
    what_it_answers_by_voice,
)
from .manifest import LaunchManifest
from .room_funestras import RoomFunestra
from .runtime_flow import write_flag_file
from .session_handoff import forget_the_kept_origenerator, kept_origenerator
from .standalone_origenerator import (
    OpenOrigenerator,
    claim_the_osr2,
    take_it_over,
    the_open_origenerator,
    the_takeover_it_was_handed,
)
from .win32_process import get_process_creation_time
from .window_layout import WindowLayoutPlan, screen_layout
from .windows_bridge_startup import (
    HandedFunestra,
    launch_origenerator,
    origenerator_interpreter,
    origenerator_launch_kwargs,
    origenerator_session_args,
)

logger = logging.getLogger(__name__)

# Said by a session adopting a kept app, which was started for the one it left.
HAND_OVER = "HAND_OVER"
TAKE_BACK = "TAKE_BACK"

ANSWER_BUDGET_S = 20.0
BOOTING_ANSWER_BUDGET_S = 90.0
_PUBLISH_POLL_S = 0.25


class HostedApp(NamedTuple):
    pid: int
    already_open: bool
    taken_over: bool
    slideshow_on: tuple[RoomFunestra, ...]


def _adopt_a_kept_origenerator(m: LaunchManifest) -> HostedApp | None:
    state_dir = Path(m.commands.origenerator_status_file).parent
    kept = kept_origenerator(state_dir)
    forget_the_kept_origenerator(state_dir)
    if kept is None or get_process_creation_time(kept.pid) != kept.created_at:
        return None  # gone since, or that pid is somebody else's now
    write_flag_file(m.commands.origenerator_paused_file, False)
    Path(m.commands.origenerator_cmd_file).write_text("", encoding="utf-8")
    logger.info("Adopted the hosted Origenerator left running (pid %d)", kept.pid)
    return HostedApp(kept.pid, already_open=True, taken_over=kept.taken_over,
                     slideshow_on=())


def _budget_for(open_app: OpenOrigenerator) -> float:
    return BOOTING_ANSWER_BUDGET_S if open_app.starting else ANSWER_BUDGET_S


def _it_took_the_takeover(open_app: OpenOrigenerator) -> bool:
    takeover = the_takeover_it_was_handed(open_app.checkout)
    give_up_at = time.monotonic() + _budget_for(open_app)
    while takeover.exists():
        if time.monotonic() >= give_up_at:
            return False
        time.sleep(_PUBLISH_POLL_S)
    return True


def _the_funestras_it_is_handed(m: LaunchManifest) -> dict[str, HandedFunestra]:
    return {
        funestra.label: HandedFunestra(
            playlist_file=m.commands.funestra_file(funestra.label, "playlist"),
            cmd_file=m.commands.funestra_file(funestra.label, "cmd"),
            status_file=m.commands.funestra_file(funestra.label, "status"),
            hud_file=m.commands.funestra_file(funestra.label, "origenerator_hud"),
        )
        for funestra in RoomFunestra.SATELLITES
    }


def the_session_it_is_handed(m: LaunchManifest, plan: WindowLayoutPlan, *,
                             in_a_headset: bool) -> dict:
    return dict(
        layout_plan=plan,
        frames_file=m.commands.origenerator_frames_file if in_a_headset else None,
        input_file=m.commands.origenerator_input_file if in_a_headset else None,
        command_file=m.commands.origenerator_cmd_file,
        paused_file=m.commands.origenerator_paused_file,
        status_file=m.commands.origenerator_status_file,
        dashboard_cmd_file=m.commands.dashboard_cmd_file,
        funestras=_the_funestras_it_is_handed(m),
    )


def what_an_adopted_app_is_told(frames_file: str, input_file: str, *, in_a_headset: bool) -> str:
    return f"{HAND_OVER}|{frames_file}|{input_file}" if in_a_headset else TAKE_BACK


def _clear_what_an_earlier_room_left(m: LaunchManifest) -> None:
    for name in (m.commands.origenerator_frames_file,
                 m.commands.origenerator_input_file):
        with contextlib.suppress(OSError):
            Path(name).unlink(missing_ok=True)


def bring_up_the_hosted_app(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan | None = None,
    sibling_checkouts: str,
    in_a_headset: bool = False,
) -> HostedApp | None:
    """Adopt, take over, or launch the app the manifest names, or None."""
    origenerator_dir = m.runtime.origenerator_dir.strip()
    if not origenerator_dir:
        return None
    if plan is None:
        plan = screen_layout(m.layout, rfb_slot_strip=m.runtime.hosts_origenerator).plan
    claim_the_osr2(origenerator_dir)
    if in_a_headset:
        _clear_what_an_earlier_room_left(m)
    kept = _adopt_a_kept_origenerator(m)
    if kept is not None:
        crossing = what_an_adopted_app_is_told(m.commands.origenerator_frames_file,
                                               m.commands.origenerator_input_file,
                                               in_a_headset=in_a_headset)
        append_command(Path(m.commands.origenerator_cmd_file), crossing)
        logger.info("Told the adopted app which room this is: %s", crossing)
        return kept
    # Both read on the app's first tick, and a room never opens paused.
    write_flag_file(m.commands.origenerator_paused_file, False)
    origenerator_cmd_file = Path(m.commands.origenerator_cmd_file)
    origenerator_cmd_file.parent.mkdir(parents=True, exist_ok=True)
    origenerator_cmd_file.write_text("", encoding="utf-8")
    Path(m.commands.origenerator_status_file).unlink(missing_ok=True)
    session = the_session_it_is_handed(m, plan, in_a_headset=in_a_headset)
    for funestra in session["funestras"].values():
        Path(funestra.hud_file).unlink(missing_ok=True)
    open_app = the_open_origenerator(origenerator_dir)
    if open_app is not None:
        take_it_over(open_app.checkout, pid=open_app.pid,
                     args=the_args_it_declares(open_app.checkout,
                                               origenerator_session_args(**session)))
        logger.info("Took over the Origenerator %s from %s (pid %d)",
                    "still starting" if open_app.starting else "already open",
                    open_app.checkout, open_app.pid)
        if _it_took_the_takeover(open_app):
            return HostedApp(open_app.pid, already_open=not open_app.starting,
                             taken_over=True, slideshow_on=open_app.slideshow_on)
        logger.warning("The Origenerator we took over (pid %d) never read the takeover "
                       "in %.0fs; launching ours instead", open_app.pid,
                       _budget_for(open_app))
    origenerator_pid = launch_origenerator(
        python_exe=_its_interpreter(m, origenerator_dir),
        origenerator_dir=origenerator_dir,
        # It imports funestra too (the shows' HUD is the Funestras'
        # shared one), so a named checkout reaches it like everyone else.
        sibling_checkouts=sibling_checkouts,
        **session,
    )
    logger.info("Origenerator launched from %s (pid %d)", origenerator_dir, origenerator_pid)
    return HostedApp(origenerator_pid, already_open=False, taken_over=False, slideshow_on=())


def _its_interpreter(m: LaunchManifest, origenerator_dir: str) -> str | Path:
    return (m.executables.origenerator_python_exe.strip()
            or origenerator_interpreter(origenerator_dir))


def what_it_answers_by_voice_in(m: LaunchManifest, **asking) -> HostedVocabulary:
    origenerator_dir = m.runtime.origenerator_dir.strip()
    if not origenerator_dir:
        return NO_HOSTED_VOCABULARY
    return what_it_answers_by_voice(
        origenerator_dir, python_exe=_its_interpreter(m, origenerator_dir),
        launch_kwargs=origenerator_launch_kwargs(
            origenerator_dir=origenerator_dir, sibling_checkouts=m.runtime.sibling_checkouts),
        **asking)
