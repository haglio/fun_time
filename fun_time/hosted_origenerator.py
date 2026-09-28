"""The hosted Origenerator, brought up the same way by either shape of session."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

from player_core.file_channel import append_command

from .manifest import LaunchManifest
from .players import Player
from .runtime_flow import write_flag_file
from .session_handoff import forget_the_kept_origenerator, kept_origenerator
from .standalone_origenerator import claim_the_osr2, take_it_over, the_open_origenerator
from .win32_process import get_process_creation_time
from .window_layout import WindowLayoutPlan, screen_layout
from .windows_bridge_startup import (
    HandedPlayer,
    launch_origenerator,
    origenerator_interpreter,
    origenerator_session_args,
)

logger = logging.getLogger(__name__)

# Said by a session adopting a kept app, which was started for the one it left.
HAND_OVER = "HAND_OVER"
TAKE_BACK = "TAKE_BACK"


class HostedApp(NamedTuple):
    pid: int
    already_open: bool
    taken_over: bool


def _adopt_a_kept_origenerator(m: LaunchManifest) -> HostedApp | None:
    state_dir = Path(m.commands.origenerator_status_file).parent
    kept = kept_origenerator(state_dir)
    forget_the_kept_origenerator(state_dir)
    if kept is None or get_process_creation_time(kept.pid) != kept.created_at:
        return None  # gone since, or that pid is somebody else's now
    write_flag_file(m.commands.origenerator_paused_file, False)
    Path(m.commands.origenerator_cmd_file).write_text("", encoding="utf-8")
    logger.info("Adopted the hosted Origenerator left running (pid %d)", kept.pid)
    return HostedApp(kept.pid, already_open=True, taken_over=kept.taken_over)


def _the_players_it_is_handed(m: LaunchManifest) -> dict[str, HandedPlayer]:
    return {
        player.label: HandedPlayer(
            playlist_file=m.commands.player_file(player.label, "playlist"),
            cmd_file=m.commands.player_file(player.label, "cmd"),
            status_file=m.commands.player_file(player.label, "status"),
            hud_file=m.commands.player_file(player.label, "origenerator_hud"),
        )
        for player in Player.SATELLITES
    }


def bring_up_the_hosted_app(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan | None = None,
    project_dirs: str,
    in_a_headset: bool = False,
) -> HostedApp | None:
    """Adopt, take over, or launch the app the manifest names, or None."""
    origenerator_dir = m.runtime.origenerator_dir.strip()
    if not origenerator_dir:
        return None
    if plan is None:
        plan = screen_layout(m.layout).plan
    claim_the_osr2(origenerator_dir)
    kept = _adopt_a_kept_origenerator(m)
    if kept is not None:
        crossing = (f"{HAND_OVER}|{m.commands.origenerator_frames_file}"
                    f"|{m.commands.origenerator_input_file}") if in_a_headset else TAKE_BACK
        append_command(Path(m.commands.origenerator_cmd_file), crossing)
        logger.info("Told the adopted app which room this is: %s", crossing)
        return kept
    # Both read on the app's first tick, and a room never opens paused.
    write_flag_file(m.commands.origenerator_paused_file, False)
    origenerator_cmd_file = Path(m.commands.origenerator_cmd_file)
    origenerator_cmd_file.parent.mkdir(parents=True, exist_ok=True)
    origenerator_cmd_file.write_text("", encoding="utf-8")
    Path(m.commands.origenerator_status_file).unlink(missing_ok=True)
    players = _the_players_it_is_handed(m)
    for player in players.values():
        Path(player.hud_file).unlink(missing_ok=True)
    contract = dict(
        layout_plan=plan,
        frames_file=m.commands.origenerator_frames_file if in_a_headset else None,
        input_file=m.commands.origenerator_input_file if in_a_headset else None,
        command_file=m.commands.origenerator_cmd_file,
        paused_file=m.commands.origenerator_paused_file,
        status_file=m.commands.origenerator_status_file,
        dashboard_cmd_file=m.commands.dashboard_cmd_file,
        players=players,
    )
    open_app = the_open_origenerator(origenerator_dir)
    if open_app is not None:
        take_it_over(open_app.checkout, pid=open_app.pid,
                     args=origenerator_session_args(**contract))
        logger.info("Took over the Origenerator %s from %s (pid %d)",
                    "still starting" if open_app.starting else "already open",
                    open_app.checkout, open_app.pid)
        return HostedApp(open_app.pid, already_open=not open_app.starting, taken_over=True)
    origenerator_pid = launch_origenerator(
        python_exe=(m.executables.origenerator_python_exe.strip()
                    or origenerator_interpreter(origenerator_dir)),
        origenerator_dir=origenerator_dir,
        # It imports player_core too (the shows' HUD is the players'
        # shared one), so a named checkout reaches it like everyone else.
        project_dirs=project_dirs,
        **contract,
    )
    logger.info("Origenerator launched from %s (pid %d)", origenerator_dir, origenerator_pid)
    return HostedApp(origenerator_pid, already_open=False, taken_over=False)
