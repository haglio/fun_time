"""Background dispatch loop for dashboard commands and genau sync.

Runs in a thread alongside the AHK hotkey script, handling periodic
dispatch directly in Python instead of spawning subprocesses.
"""
from __future__ import annotations

import configparser
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from player_core.file_channel import append_command
from player_core.funscript import PARK_TOUCH_WAIT_CAP_MS

from .command_dispatch import (
    FAILED_NOTICE_LEVEL,
    MAIN_SIDE,
    BridgeConfig,
    BridgeState,
    dispatch_command,
    routes_to_origenerator,
    side_name,
)
from .dashboard_actions import HELP_REFERENCE_COMMANDS
from .event_log import FAVORITE, NOTICE, notice
from .hud_transport import HudPublisher
from .library_browser import browse_library
from .lock_hud import SideInputs, build_panels, origenerator_mode_panel
from .manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME
from .mode_plan import genau_active
from .nau_console import console_payload
from .modes import build_mirrored_funscript_path, is_favorite_path, read_favs_content
from .satellite_control import read_satellite_status
from .satellites_mode import origenerator_shows
from .shared_state import read_shared_state, write_shared_state
from .voice_commands import parse_command_line
from .watch_sampling import WatchSampler
from .watch_stats import watch_stats_path
from .windows_bridge_random_favs_browser import open_rfb_tab
from .voice_control import SUSPEND_EXEMPT_COMMANDS, VoiceController
from .dashboard_bridge import write_dashboard_snapshot
from .dashboard_runtime import (
    genau_status_path,
    is_broker_heartbeat_fresh,
    is_osr2_device_on,
    read_genau_status,
    read_nau_status,
)
from .runtime_flow import read_flag_file
from .role_windows import WindowRoles
from .windows_bridge_startup import launch_broker_tray, stop_broker_processes
from .window_roles import visible_roles
from .win32 import force_foreground_window, window_exists, window_rect

logger = logging.getLogger(__name__)


# What Nau's own notice levels mean here.  Nau has no palette — it names the kind
# of thing that happened and this side picks the color, the same way the ops
# raised in :mod:`fun_time.command_dispatch` do.
_NAU_NOTICE_LEVELS = {"error": FAILED_NOTICE_LEVEL, "favorite": FAVORITE}



def read_nau_notice(path) -> tuple[float, str, str]:
    """Nau's latest one-shot notice as (sequence, level, message).

    Nau bumps the sequence whenever it raises one; (0, "", "") means there is
    nothing to read. Only Nau knows whether a clip jump had a target, so this is
    how "full video not available" reaches the overlay.
    """
    try:
        values = dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        return float(values.get("seq", "0")), values.get("level", "notice"), values.get("message", "")
    except (OSError, ValueError):
        return 0, "", ""


def poll_dashboard_commands(cmd_file: Path) -> list[str]:
    """Read and delete the dashboard command file, returning all queued commands."""
    if not cmd_file.exists():
        return []
    try:
        # Atomically move then read — any concurrent writes create a new file.
        # replace() is used instead of rename() because on Windows rename()
        # fails if the target already exists (e.g. stale .processing from a crash).
        tmp = cmd_file.with_suffix(".processing")
        cmd_file.replace(tmp)
        text = tmp.read_text(encoding="utf-8-sig").strip()
        tmp.unlink()
    except OSError:
        return []
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


# The side-agnostic actions the main player (Nau) answers, and what it answers with.
# Navigation is the same gesture on every player; "end loop" is the same *word* for
# a different loop — Nau's A-B loop rather than a satellite's group loop.  A lock
# is the same thing on all three — repeat-one on what is on screen — so the bare
# word reaches whichever was last addressed, the main player included.  So is F-mode,
# though what it narrows to differs: the favorites on a satellite, the videos
# with a funscript on the main player.
_PRIMARY_EQUIVALENTS = {
    "next": "main_next",
    "prev": "main_prev",
    "no_loop": "nau_loop_cancel",
    "lock_on": "main_lock_on",
    "lock_off": "main_lock_off",
    "fmode": "main_fmode",
    "fmode_on": "main_fmode_on",
    "fmode_off": "main_fmode_off",
    # And so is a reset — "drop whatever is narrowing this player", which on the
    # main player is its length mode and its F-mode where on a satellite it is the
    # act filter and the loop.  Without this a bare "reset" said after navigating
    # the main player reached nothing at all.
    "reset": "main_reset",
    # So are the two browse orders: every player browses newest-first or shuffled,
    # and the main player is no exception now that Genau answers them too.  Left
    # out, a bare "latest" said while the main player was the active one resolved
    # to a command with no handler and did nothing at all — the player had to be
    # named ("main latest") for a word that is supposed to reach whoever is active.
    "latest": "main_latest",
    "shuffle": "main_shuffle",
}


def resolve_active_side_command(command: str, active_side: int) -> str:
    """Rewrite a side-agnostic ``active_*`` command onto the active player.

    ``active_next``/``active_prev`` follow the last player navigated — main
    (Nau, slot 1), portrait (2), or landscape (3).  ``active_lock_on``/``_off``
    reach the main player too, meaning there what they mean on a satellite: hold the
    video on screen, or let the playlist walk on.  So does ``active_no_loop``, but
    meaning the loop *it* has: Nau's A-B loop, where on a satellite the same phrase
    ends a group loop.  The rest (weird, cycle) exist only on the satellites, so
    while the main player is active they resolve to nothing — returned unchanged, which
    is a no-op downstream.  Every non-``active_`` command passes through unchanged.
    """
    if not command.startswith("active_"):
        return command
    action = command[len("active_"):]
    if active_side == 1:
        # What each side-agnostic action means on the main player; anything absent
        # here simply has no main-player equivalent.
        return _PRIMARY_EQUIVALENTS.get(action, command)
    prefix = "portrait_" if active_side == 2 else "landscape_"
    return prefix + action


def expand_both_command(command: str) -> list[str]:
    """Expand a ``both_*`` command into its Portrait + Landscape pair.

    Saying "both next" enqueues ``both_next``; there is no combined handler —
    a both-command is just sugar for driving each satellite in turn (Portrait
    first) through the exact same per-command handling as "portrait next" /
    "landscape next".  Any other command passes through unchanged.
    """
    if command.startswith("both_"):
        suffix = command[len("both_"):]
        return [f"portrait_{suffix}", f"landscape_{suffix}"]
    return [command]


def detect_sleep_gap(prev_wall: float, now_wall: float, *, threshold_s: float = 90.0) -> float | None:
    """Elapsed seconds if the loop stalled far longer than its tick cadence.

    The dispatch thread freezes while Windows is asleep or in modern standby;
    on resume the wall clock has jumped forward by the sleep duration.  A gap
    far above the ~50 ms tick interval means we just woke — the moment AHK's
    hotkeys are prone to not firing until the bridge is restarted.  Returns
    None for ordinary iterations (and for merely slow ticks, e.g. a blocking
    file read against a stalled disk, which the threshold clears).
    """
    gap = now_wall - prev_wall
    return gap if gap >= threshold_s else None


class DispatchLoopRunner:
    """Runs dashboard polling and genau sync in-process."""

    def __init__(
        self,
        *,
        config: BridgeConfig,
        dashboard_cmd_file: Path,
        shared_state_file: Path,
        ahk_cmd_file: Path,
        windows: WindowRoles,
        dashboard_enabled: bool,
        manifest_path: Path | None = None,
        hud_publisher: HudPublisher | None = None,
        rfb_shortcut_target: str = "",
        rfb_shortcut_work_dir: str = "",
        rfb_shortcut_args: str = "",
        sync_interval_ms: int = 200,
    ) -> None:
        self.config = config
        self.dashboard_cmd_file = dashboard_cmd_file
        self.shared_state_file = shared_state_file
        self.ahk_cmd_file = ahk_cmd_file
        # The session's launch manifest — handed to the library browser, which
        # reads the same file every other child process does, so the browse can
        # never disagree with the session about what the library is.
        self.manifest_path = manifest_path or (
            config.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        # Every window the session manages, and the one cache of their HWNDs:
        # the tick and the library browser's own thread both go through here.
        self.windows = windows
        self.dashboard_enabled = dashboard_enabled
        # The lock HUD's model: this loop holds the state the map is drawn from
        # (locks, filters, loops) and already ticks, so it builds each satellite's
        # panel and publishes it for that satellite's player to render into its
        # own video.  None when the session runs without HUDs.
        self._hud_publisher = hud_publisher
        self._last_hud_publish = 0.0
        # The favorites list, and the stat that says whether it has moved (see
        # _favs_content) — every HUD publish asks whether the clip on screen is on it.
        self._favs_text = ""
        self._favs_stamp: tuple[int, int] | None = None
        # The clip each satellite last named, so a status read that loses the
        # race with the player's own republish does not blank its map.
        self._last_satellite_clip: dict[str, str] = {}
        self.rfb_shortcut_target = rfb_shortcut_target
        self.rfb_shortcut_work_dir = rfb_shortcut_work_dir
        self.rfb_shortcut_args = rfb_shortcut_args
        self.sync_interval_s = sync_interval_ms / 1000
        self.state = BridgeState()
        self._last_sync = 0.0
        self._stop = threading.Event()
        self._browse_lock = threading.Lock()
        # The browse now on screen, if any.  It is launched mid-session, so it
        # cannot join the startup children the teardown list kills; this holds it
        # instead, and stop() is where quitting takes it with the session.
        self._browser_process: subprocess.Popen | None = None
        self._press_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._press_port: int | None = None
        self._press_port_file = config.state_dir / "dashboard_press_port.txt"
        # RFB tabs opened by locks are buffered and opened in one Chrome launch
        # per poll batch: "lock both" locks two videos in one tick, and two
        # rapid chrome.exe launches race Chrome's singleton and drop a tab.
        self._pending_rfb_urls: list[str] = []
        self._batching_rfb = False
        # Latch whatever is already on disk, so a notice left over from a
        # previous session does not flash the moment this one opens.
        self._last_nau_notice_seq = read_nau_notice(
            getattr(config, "nau_notice_file", None) or Path("nau_notice.txt")
        )[0]
        self.voice_controller: VoiceController | None = None
        # Watch tracking ("breeding"): every player's current clip, sampled and
        # classified into completions and skips for the stats file.
        self.watch = WatchSampler(
            nau_status_file=config.nau_status_file,
            satellite_status_files={2: config.portrait_status_file,
                                    3: config.landscape_status_file},
            stats_file=watch_stats_path(config.state_dir),
        )
        # Hybrid funscript handoff: whether the funscript is driving the OSR2
        # right now (so Genau is paused and Nau's T-Code is on) or Genau is (a
        # funscript gap or an unscripted video).  None means "no decision applied
        # yet" — set outside hybrid so re-entry re-asserts the correct driver.
        self._hybrid_funscript_driving: bool | None = None
        self._hybrid_asserted_at: float = 0.0
        self._nau_status_snapshot = None
        # When the park-touch hold releases the pending Genau-to-script flip;
        # None outside one — see _holding_for_park_touch.
        self._park_touch_deadline: float | None = None

    # How often the standing hybrid pair (SET_TCODE_ENABLED + PAUSE/RESUME) is
    # re-queued without an edge, so a verb lost in transit converges instead of
    # staying lost until the next turn boundary.
    _HYBRID_REASSERT_S = 1.0

    # The satellites play ~5 s clips, so the HUD map has to track the current clip
    # almost the instant it changes — but not at the loop's own 20 Hz.  Building a
    # panel is index lookups plus a stat per thumbnail, and the publisher skips the
    # write entirely when the panel is unchanged, so an idle tick is nearly free.
    _HUD_PUBLISH_INTERVAL_S = 0.15

    def tick(self) -> None:
        """Run one iteration: poll dashboard, maybe sync genau."""
        self._flash_nau_notice()

        # Sync state from shared file — AHK hotkey dispatches update it directly.
        shared = read_shared_state(self.shared_state_file)
        if shared is not None:
            self.state = shared

        # Hand the OSR2 to the current video's funscript (or back to Genau).
        # Runs before the command loop so a mode switch that also writes
        # genau_cmd (RESUME + HUD_ON on entering hybrid) is never clobbered by
        # the handoff in the same tick — the handoff instead lands next tick,
        # once that entry is on the current, now-hybrid mode.
        self._sync_hybrid_driver()

        # Dashboard commands (may be multiple if queued by rapid hotkey
        # presses).  Each raw line yields a command plus, for a spoken one, when
        # the utterance began; the command is then bound to concrete side
        # command(s): a side-agnostic "active_*" command (voice "lock", "next",
        # ...) resolves to whichever satellite was most recently addressed — by
        # voice or by keyboard nav — and a "both_*" command expands into its
        # Portrait + Landscape pair.
        # Buffer RFB opens across the whole batch so a "both" lock's two tabs
        # open in one Chrome launch (see _flush_rfb_tabs).
        self._batching_rfb = True
        try:
            for line in poll_dashboard_commands(self.dashboard_cmd_file):
                raw_command, spoken_at = parse_command_line(line)
                resolved = resolve_active_side_command(raw_command, self.state.active_side)
                for command in expand_both_command(resolved):
                    self._handle_command(command, spoken_at)
        finally:
            self._batching_rfb = False
        self._flush_rfb_tabs()
        # After the batch, so a switch and a switch straight back inside one
        # batch cancel rather than minimize the player they just brought back.
        self.windows.flush_pending_hides()

        self._sync_voice_suspension()

        # Periodic sync: z-order enforcement and dashboard update
        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            self._converge_origenerator_window()
            if self.dashboard_enabled:
                self._update_dashboard()
        self.watch.sample_due(now=now, paused=self.state.omni_paused)
        if now - self._last_hud_publish >= self._HUD_PUBLISH_INTERVAL_S:
            self._last_hud_publish = now
            self._publish_huds()

    def _publish_huds(self) -> None:
        """Rebuild both satellites' HUD panels and publish the ones that changed.

        Runs under OmniPause too: playback is frozen, but the map stays up so the
        user can still see — and click — what each satellite is holding.
        """
        if self._hud_publisher is None:
            return
        state = self.state
        favs = self._favs_content()

        def side(name: str, *, sources: str, status_file: Path, locked: bool) -> SideInputs:
            current = self._satellite_clip(name, status_file)
            return SideInputs(
                side=name, sources=sources, current=current, locked=locked,
                filter_query=getattr(state, f"{name}_filter"),
                loop_axis=getattr(state, f"{name}_loop"),
                map_anchor=getattr(state, f"{name}_map_anchor"),
                widen_clip=getattr(state, f"{name}_widen_clip"),
                nav_anchor=getattr(state, f"{name}_nav_anchor"),
                latest=getattr(state, f"{name}_latest"),
                f_mode=getattr(state, f"{name}_f_mode"),
                is_favorite=is_favorite_path(current, favs),
            )

        if self.config.origenerator_enabled and origenerator_shows(state.satellites_mode):
            # The players are black and paused for the whole mode: a clip map
            # here would be thumbnails of videos nobody is being shown.  The
            # sides say the mode instead (status + the mode row home); a show
            # covering a region wears its own map of the origenerator items.
            portrait = origenerator_mode_panel(
                "portrait", active=side_name(state.active_side) == "portrait")
            landscape = origenerator_mode_panel(
                "landscape", active=side_name(state.active_side) == "landscape")
        else:
            portrait, landscape = build_panels(
                side("portrait", sources=self.config.portrait_sources,
                     status_file=self.config.portrait_status_file, locked=state.locked2),
                side("landscape", sources=self.config.landscape_sources,
                     status_file=self.config.landscape_status_file, locked=state.locked3),
                metadata_root=self.config.regen_metadata_root,
                active_side=side_name(state.active_side),
                # "" for a session hosting no Origenerator — the HUDs then draw no
                # mode pair at all, rather than a switch that can only dead-end.
                satellites_mode=(state.satellites_mode
                                 if self.config.origenerator_enabled else ""),
            )
        self._hud_publisher.publish("portrait", portrait)
        self._hud_publisher.publish("landscape", landscape)
        # The main console: the controls the dashboard used to hold for
        # whichever player owns the slot, what has the OSR2, whether the broker is
        # up, and which player a bare command reaches — none of which the player
        # can see for itself.
        nau = read_nau_status(self.config.nau_status_file)
        self._hud_publisher.publish_payload("nau", console_payload(
            mode=state.main_mode,
            active=state.active_side == MAIN_SIDE,
            f_mode=state.main_f_mode,
            latest=state.main_latest,
            genau_latest=state.genau_latest,
            osr2_mode=self._osr2_mode(),
            funscript_driving=nau.funscript_driving,
            broker=is_broker_heartbeat_fresh(self.config.broker_heartbeat_file)
            if self.config.broker_heartbeat_file else False,
            # Nau's loop machine, so the record button on the console can show
            # which half of the gesture is running, and its lock, so the padlock
            # can show whether the video is being held.  Both come back off Nau's
            # own status file, because in genau mode the player drawing that
            # console has neither to ask.
            record=nau.state,
            nau_locked=nau.locked,
            genau=read_genau_status(genau_status_path(self.config.state_dir)),
        ))

    def _favs_content(self) -> str:
        """The favorites file, re-read only when it has actually changed.

        Each HUD publish asks whether the clip on screen is a favorite, ~7x a
        second for the life of the session; the list itself moves a handful of
        times an hour, so gate the read on the file's mtime and size and keep the
        text between changes.
        """
        try:
            stat = self.config.favs_file.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if stamp != self._favs_stamp:
            self._favs_stamp = stamp
            self._favs_text = read_favs_content(self.config.favs_file)
        return self._favs_text

    def _satellite_clip(self, side: str, status_file: Path) -> str:
        """The clip *side* is showing, holding the last one it named if the read
        comes back blank.

        A satellite always has a clip — it cannot discard its way to an empty
        playlist — so once one has named a clip, a blank status means the read
        lost a race with the player's own republish, not that the player has
        nothing.  Believing the blank builds an empty panel, and publishing that
        blanks the map on screen until the next tick puts it back.  Before a
        satellite's first status there is nothing to hold, and an empty map is
        the truth.
        """
        video = read_satellite_status(status_file).video
        if video:
            self._last_satellite_clip[side] = video
            return video
        return self._last_satellite_clip.get(side, "")

    def _sync_voice_suspension(self) -> None:
        """Freeze voice while omnipause holds, as AHK's ``Suspend`` freezes the keys.

        The suspend_hotkeys WindowOp only reaches AHK; voice lives in this
        process, so it is driven off ``omni_paused`` itself — the one authority
        both the dashboard and the shared state file agree on.  Suspended, only
        the exempt commands (resume, quit, relief) still write, mirroring the AHK
        script's ``#SuspendExempt`` block.
        """
        if self.voice_controller is None:
            return
        if self.state.omni_paused:
            self.voice_controller.suspend()
        else:
            self.voice_controller.unsuspend()

    def _flash_nau_notice(self) -> None:
        """Surface anything Nau has raised since the last tick, once.

        Nau names the kind rather than the color: "error" for a request with
        nowhere to go, "favorite" for one about a funscript — which is what green
        is kept for here — and anything else is an ordinary white notice.
        """
        path = getattr(self.config, "nau_notice_file", None)
        if path is None:
            return
        seq, level, message = read_nau_notice(path)
        if seq <= self._last_nau_notice_seq:
            return
        self._last_nau_notice_seq = seq
        if message:
            notice(
                logger, message, source="main",
                level=_NAU_NOTICE_LEVELS.get(level, NOTICE),
            )

    def _sync_hybrid_driver(self) -> None:
        """In hybrid, route the OSR2 to the funscript or Genau, moment to moment.

        Genau and a funscript both feed the broker's one UDP T-Code inlet, so
        only one may drive at a time.  The funscript drives while it is actively
        scripting (``has_funscript`` and not ``funscript_resting``); Genau drives
        the unscripted stretches — a video without a funscript, or a funscript's
        quiet lead-in and interior gaps.  Each handoff sets both levers: Nau's
        T-Code on + Genau paused for the funscript, or Nau's T-Code off (so its
        gap drift can't fight) + Genau resumed for Genau.  It is edge-triggered,
        so it fires once per handoff, not every tick.  Outside hybrid (or under
        omnipause) the remembered state is cleared so re-entry re-asserts the
        driver; leaving hybrid re-enables Nau's T-Code via the mode switch.

        The handoff itself is not smoothed here, and nothing waits for the
        stroke: whoever takes the device walks it from where it is to where it
        needs to be (Nau's driver parks it over its handoff ramp; Genau climbs
        back out of the park over the same one).  Waiting here for Genau's next
        floor-touch made the moment depend on the live stroke, and the trace —
        which had to draw that moment before it happened — could only guess it.
        """
        if self.state.main_mode != "hybrid" or self.state.omni_paused:
            self._hybrid_funscript_driving = None
            self._park_touch_deadline = None
            return
        previous = self._nau_status_snapshot
        status = read_nau_status(self.config.nau_status_file, fallback=previous)
        self._nau_status_snapshot = status
        funscript_driving = status.funscript_driving
        now = time.monotonic()
        if (funscript_driving == self._hybrid_funscript_driving
                and now - self._hybrid_asserted_at < self._HYBRID_REASSERT_S):
            return
        if funscript_driving and self._hybrid_funscript_driving is False:
            # Taking the device FROM Genau: a stroke whose floor rests ON the
            # park is set down exactly where the trace draws its blue ending —
            # on its next touch-down — so the flip holds for that one touch.
            # A raised floor takes the ramp instead and flips at once.  Only a
            # FLOWING boundary crossing holds: entered by a seek, there is no
            # drawn blue ending to honor — the trace shows the script's turn
            # already running — and a hold there kept Genau swinging under a
            # pure green picture for its whole cap.  Nothing re-asserts during
            # a hold; the standing pair still says Genau, which is the truth
            # of it.
            flowed = (previous is not None
                      and abs(status.position_ms - previous.position_ms) < 1_500)
            if flowed and self._holding_for_park_touch(now, status):
                return
        else:
            self._park_touch_deadline = None
        # ASSERTED, not fired-and-forgotten.  A verb queued on a file channel
        # can still die — a writer replacing the file whole, a drain racing the
        # append, a locked file exhausting the retries — and an edge-triggered
        # arbiter that assumed delivery left the session split-brained for a
        # whole cluster: Genau paused, the funscript never enabled, everything
        # idle and grey.  So the edge is recorded only once both verbs actually
        # queued, and the standing pair is re-queued on a slow heartbeat — both
        # verbs are idempotent at their players — so any lost one converges
        # within a second instead of at the next turn boundary.
        queued_nau = append_command(
            self.config.nau_cmd_file,
            "SET_TCODE_ENABLED 1" if funscript_driving else "SET_TCODE_ENABLED 0",
        )
        queued_genau = append_command(
            self.config.genau_cmd_file,
            "PAUSE" if funscript_driving else "RESUME",
        )
        if queued_nau and queued_genau:
            self._hybrid_funscript_driving = funscript_driving
            self._hybrid_asserted_at = now
            self._park_touch_deadline = None

    def _holding_for_park_touch(self, now: float, status) -> bool:
        """Whether the Genau-to-script flip is still waiting for a touch-down.

        The touch is NAU'S CHOICE, published with its status: the trace picks
        one touch-down, draws the blue ending on it, and this side simply ends
        Genau's turn when the playhead reaches it — one chooser, so the device
        cannot stop at a different trough than the picture drew.  When each
        side chose from its own read of the wave, the arbiter could take an
        earlier touch, and the leftover drawn blue vanished the moment the dot
        reached it.  No published touch means the ramp case: flip at once.
        The wall-clock cap keeps a stalled playhead from holding forever.
        """
        touch = status.handoff_touch_ms
        if touch is None or status.position_ms >= touch:
            self._park_touch_deadline = None
            return False
        if self._park_touch_deadline is None:
            self._park_touch_deadline = now + PARK_TOUCH_WAIT_CAP_MS / 1000
        if now < self._park_touch_deadline:
            return True
        self._park_touch_deadline = None
        return False

    def _handle_command(self, cmd: str, spoken_at: float | None = None) -> None:
        """Route one polled command (already expanded from any ``both_*``).

        ``spoken_at`` is when a voice command's utterance began, and None for
        the instantaneous hotkey and dashboard presses.
        """
        if (
            self.state.omni_paused
            and spoken_at is not None
            and cmd not in SUSPEND_EXEMPT_COMMANDS
        ):
            # Freeze SPOKEN commands while paused — a mis-heard phrase must not
            # act on a paused room.  ``spoken_at`` marks a voice line; the
            # deliberate mouse (dashboard, lock HUD) stays live because a click
            # is not an accident.  This backstops VoiceController's own suspend,
            # closing the entry race where a phrase is written in the tick before
            # the suspend flag is set.  Exempts the same resume/quit/relief
            # voice does.
            logger.debug("OmniPause dropped spoken command: %s", cmd)
            return
        self._send_press(cmd)
        if cmd == "quit":
            self.ahk_cmd_file.write_text("exit", encoding="utf-8")
            return
        if cmd in HELP_REFERENCE_COMMANDS:
            # Pure dashboard-UI action: the press above tells the dashboard to
            # toggle/close the hotkeys/voice popup — nothing to dispatch here.
            return
        if cmd == "omniminimize":
            self._handle_omniminimize()
        elif cmd == "omnirestore":
            self._handle_omnirestore()
        elif cmd == "omnipause_toggle":
            self._handle_omnipause_toggle()
        elif cmd == "enter_omnipause":
            if not self.state.omni_paused:
                self._handle_enter_omnipause("enter_omnipause")
        elif cmd == "relief_omnipause":
            # No already-paused guard, unlike Space above: a session can be paused
            # with the device still on the user, which is the case relief exists
            # for, so the retract must go out even from inside omnipause.
            self._handle_enter_omnipause("relief_omnipause")
        elif cmd == "browse_library":
            threading.Thread(
                target=self._handle_browse_library,
                daemon=True,
                name="library-browser",
            ).start()
        elif cmd == "broker_panel":
            threading.Thread(
                target=self._handle_broker_toggle,
                daemon=True,
                name="broker-toggle",
            ).start()
        elif cmd == "backslash_key":
            if genau_active(self.state.main_mode):
                self._send_press("quarter_button")
                self._dispatch("quarter_button", spoken_at)
            else:
                self._send_press("browse_library")
                threading.Thread(
                    target=self._handle_browse_library,
                    daemon=True,
                    name="library-browser",
                ).start()
        # -- idempotent voice commands --
        elif cmd == "pause":
            if not self.state.omni_paused:
                self._handle_omnipause_toggle()
        elif cmd == "play":
            if self.state.omni_paused:
                self._handle_omnipause_toggle()
        elif cmd == "portrait_lock_on":
            if not self.state.locked2:
                self._dispatch("portrait_lock", spoken_at)
        elif cmd == "landscape_lock_on":
            if not self.state.locked3:
                self._dispatch("landscape_lock", spoken_at)
        elif cmd == "portrait_lock_off":
            if self.state.locked2:
                self._dispatch("portrait_lock", spoken_at)
        elif cmd == "landscape_lock_off":
            if self.state.locked3:
                self._dispatch("landscape_lock", spoken_at)
        elif cmd == "broker_start":
            self._handle_broker_start()
        elif cmd == "broker_stop":
            self._handle_broker_stop()
        elif cmd in ("voice_off", "voice_toggle"):
            self._handle_voice_toggle(cmd)
        else:
            self._dispatch(cmd, spoken_at)

    def _dispatch(self, command: str, spoken_at: float | None = None) -> None:
        logger.info("Dispatching command: %s", command)
        # A transport verb bound for an Origenerator show steps that show, not
        # the paused player underneath — booking it here would classify the
        # player's frozen clip as skipped or discarded when nobody touched it.
        if not routes_to_origenerator(command, self.state, self.config):
            self.watch.note_command(command)
        new_state, ops = dispatch_command(
            command, self.state, self.config,
            target_path=self.watch.video_at(command, spoken_at),
        )
        self.state = new_state
        suppress_unsuspend = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
        for op in ops:
            if op.op == "show_role":
                self.windows.show(op.key)
                continue
            if op.op == "hide_role":
                self.windows.hide_after_settle(op.key)
                continue
            if op.op == "minimize_role":
                # A player's own HUD minimize button, parking that one window.
                # Straight away, unlike the main-slot hide above: that one waits
                # out a settle so the player it is leaving can present its black
                # first, and nothing here has been told to blank — the window is
                # going down still showing its video, which is what the user
                # pressed for, and its frozen thumbnail is then the clip it holds.
                self.windows.park(op.key)
                continue
            if op.op == "restore_parked":
                self.windows.restore_parked()
                continue
            if op.op == "activate_role":
                if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
                    self.windows.activate(op.key)
                continue
            if op.op == "restack_main":
                # Re-stack the overlapping Nau/Genau pair for the current mode.
                # Not integration-guarded: SetWindowPos(HWND_TOPMOST) uses
                # SWP_NOACTIVATE, so it changes only the z-band, never focus.
                self.windows.restack_main_slot(self.state.main_mode)
                continue
            if op.op == "restack_satellites":
                self.windows.restack_satellites(
                    self.state.main_mode, self.state.satellites_mode)
                continue
            if op.op == "disable_all_topmost":
                self.windows.remove_all_topmost()
                continue
            if op.op == "restore_all_topmost":
                self.windows.restore_all_topmost(
            self.state.main_mode, self.state.satellites_mode)
                continue
            if suppress_unsuspend and op.op == "unsuspend_hotkeys":
                continue
            if op.op == "open_rfb_tab":
                self._pending_rfb_urls.append(op.key)
                continue
            if op.op == "notice":
                notice(logger, op.key, source=op.source, level=op.level)
            else:
                self.ahk_cmd_file.write_text(op.op, encoding="utf-8")
        write_shared_state(self.shared_state_file, self.state)
        # Outside a poll batch (e.g. a lone lock) there is nothing to coalesce
        # with, so open immediately; within a batch the tick flushes once.
        if not self._batching_rfb:
            self._flush_rfb_tabs()
        if self.dashboard_enabled:
            self._update_dashboard()

    def _flush_rfb_tabs(self) -> None:
        """Open every buffered RFB URL as tabs in the session's own Chrome window.

        The RFB runs in the user's own Chrome — his user data directory, his
        profile — so his personal windows of that profile are candidates for the
        tab too, and nothing about handing Chrome a URL says which window is
        meant.  A second chrome.exe finds the first one's singleton (it is keyed
        on the user data directory) and forwards the command line; the running
        browser resolves the profile from --profile-directory and asks
        ``FindTabbedBrowser`` for a window, which walks its browsers
        most-recently-active first and takes the first one whose profile matches.
        So the window he touched last wins — a personal window he was reading a
        moment ago beats the RFB, and the lock's tab lands there, behind the
        players where he won't see it until later.

        Activating the RFB window first is what settles that: it goes to the head
        of Chrome's own activation order, so it is the window ``FindTabbedBrowser``
        returns.  Chrome shows the window it opens into either way, so this costs
        no raise that was not already coming — it only decides which window rises.

        A dead handle means Fun Time has no window of its own left to open into,
        and every URL handed over then is guaranteed to land in one of his: that
        is worth losing the tab over, so the launch is skipped entirely.

        In origenerator mode the buffer holds instead of flushing: the RFB is
        under the hosted app's window, and opening a tab would force Chrome over
        it.  The locks queue, and switching back to player mode flushes them.
        """
        if origenerator_shows(self.state.satellites_mode):
            return
        urls = self._pending_rfb_urls
        self._pending_rfb_urls = []
        if not urls or not self.rfb_shortcut_target:
            return
        if not window_exists(self.windows.rfb_hwnd):
            logger.warning(
                "RFB tab(s) skipped: no Random Favs Browser window to open into: %s",
                ", ".join(urls),
            )
            return
        if not force_foreground_window(self.windows.rfb_hwnd):
            # Not fatal, and expected on the integration suite's hidden desktop,
            # which has no foreground window to become.
            logger.info("RFB window did not take the foreground before the tab handoff")
        open_rfb_tab(
            urls=urls,
            shortcut_target=self.rfb_shortcut_target,
            shortcut_work_dir=self.rfb_shortcut_work_dir,
            shortcut_args=self.rfb_shortcut_args,
        )
        logger.info("Opened RFB tab(s): %s", ", ".join(urls))

    def _send_press(self, action: str) -> None:
        if not self.dashboard_enabled:
            return
        try:
            if self._press_port is None:
                if self._press_port_file.exists():
                    self._press_port = int(self._press_port_file.read_text(encoding="utf-8").strip())
            if self._press_port is not None:
                self._press_socket.sendto(action.encode("utf-8"), ("127.0.0.1", self._press_port))
        except (OSError, ValueError):
            pass

    def _osr2_mode(self) -> str:
        """What the device is doing: "off" when nothing is on the wire at all,
        "auto" while Genau has claimed it, "controlled" otherwise.

        Read by the dashboard's snapshot and by Nau's console — one rule, so the
        two cannot disagree about what has the OSR2.

        "Off" requires BOTH serial stamps stale.  The device only emits bytes in
        reply to traffic, so the RX stamp alone goes quiet during any stretch
        nothing new is sent — an OmniPause, a handoff buffer — and calling that
        "off" told the console nobody had the device at the exact moments the
        handoff was on screen: the whole readout went dead grey with the dot
        parked, mid-picture, over and over.  A driver sending (TX fresh) is a
        device in use, whatever it last said back.
        """
        rx_fresh = is_osr2_device_on(self.config.osr2_serial_rx_file)
        tx_fresh = is_osr2_device_on(self.config.osr2_serial_tx_file)
        if not (rx_fresh or tx_fresh):
            return "off"
        return "auto" if read_flag_file(self.config.genau_mode_file, False) else "controlled"

    def _update_dashboard(self) -> None:
        try:
            voice_active = self.voice_controller is not None and not self.voice_controller.is_muted
            write_dashboard_snapshot(
                str(self.config.dashboard_state_file),
                omni_paused=self.state.omni_paused,
                voice_active=voice_active,
            )
        except Exception:
            pass

    def _converge_origenerator_window(self) -> None:
        """Converge the hosted app's window — never during OmniPause, whose
        window state is its own."""
        if self.state.omni_paused:
            return
        self.windows.converge_origenerator_window(
            self.state.main_mode, self.state.satellites_mode)

    def _broker_heartbeat_is_fresh(self) -> bool:
        """Whether the broker is currently talking to the OSR2 — not whether it exists.

        osr2_broker writes the heartbeat only while it holds the serial port, so
        a stale one means "no broker reaching the device", which a live broker
        with the OSR2 switched off satisfies.  Reading this as "is the broker
        alive" is what had the start paths killing healthy brokers.
        """
        hb = self.config.broker_heartbeat_file
        return hb is not None and is_broker_heartbeat_fresh(hb)

    def _handle_voice_toggle(self, cmd: str) -> None:
        """Mute or toggle voice control, then refresh the dashboard."""
        if self.voice_controller is None:
            return
        if cmd == "voice_off":
            self.voice_controller.mute()
        elif self.voice_controller.is_muted:
            self.voice_controller.unmute()
        else:
            self.voice_controller.mute()
        if self.dashboard_enabled:
            self._update_dashboard()

    def _handle_broker_toggle(self) -> None:
        """Stop the broker if it is running, start it if it is not.

        Only the stopping half may kill.  Starting launches over whatever is
        there, because the heartbeat this reads goes stale on a live broker
        whenever the OSR2 is off.
        """
        if self._broker_heartbeat_is_fresh():
            stop_broker_processes()
        else:
            launch_broker_tray(self.config.broker_tray_launcher)

    def _handle_broker_start(self) -> None:
        """Start the broker if the heartbeat says none is running.

        A start, never a restart.  The heartbeat only ticks while the broker
        holds the serial port, so a broker that cannot reach a powered-off OSR2
        reads as dead while it is alive and still serving every other client --
        and it used to be killed here, then not relaunched at all, because no
        tray launcher was passed.
        """
        if not self._broker_heartbeat_is_fresh():
            threading.Thread(
                target=lambda: launch_broker_tray(self.config.broker_tray_launcher),
                daemon=True,
                name="broker-start",
            ).start()

    def _handle_broker_stop(self) -> None:
        """Stop broker only if currently running."""
        if self._broker_heartbeat_is_fresh():
            threading.Thread(
                target=stop_broker_processes,
                daemon=True,
                name="broker-stop",
            ).start()

    def _handle_omniminimize(self) -> None:
        """Minimize the windows the current mode shows — the "omniminimize" command.

        Only mode-visible windows are minimized (SW_MINIMIZE would drag a
        hidden slot-mate back into view), each with ``activate=False`` so
        minimizing one never yanks focus to the next.  The minimized set is
        remembered so omnirestore brings back exactly these windows.
        """
        self.windows.minimize_all(
            visible_roles(self.state.main_mode, self.state.satellites_mode))

    def _handle_omnirestore(self) -> None:
        """Un-minimize exactly the windows omniminimize minimized.

        That set is every window the mode had on screen, so it covers any that a
        HUD button had already parked — they are up again, and the parked list is
        dropped so a later resume does not "restore" windows already restored.
        """
        self.windows.restore_minimized()

    def _log_topmost_state(self, label: str) -> None:
        logger.info("Topmost [%s] mode=%s: %s", label, self.state.main_mode,
                    self.windows.topmost_report())

    def _handle_omnipause_toggle(self) -> None:
        """Toggle omnipause with topmost management for all windows.

        Topmost removal (enter) and restoration (leave) are driven by
        the disable_all_topmost / restore_all_topmost WindowOps that
        command_dispatch emits — _dispatch handles them automatically.
        """
        was_paused = self.state.omni_paused
        self._dispatch("omnipause_toggle")
        self._log_topmost_state("post-leave" if was_paused else "post-enter")

    def _handle_enter_omnipause(self, command: str) -> None:
        """Enter omnipause with topmost management — enter only, no leave.

        *command* is the way in: ``enter_omnipause`` (Space) parks the OSR2,
        ``relief_omnipause`` (Shift+Esc) retracts it instead.  Everything else
        about the entry is the same, topmost removal included — that is driven by
        the disable_all_topmost WindowOp command_dispatch emits, which _dispatch
        handles automatically.
        """
        self._dispatch(command)
        self._log_topmost_state("post-enter")

    def _handle_browse_library(self) -> None:
        """Browse the library and play the pick in Nau, one browse at a time.

        Serialized on a lock: the browser is the user's window, not the dispatch
        loop's, so a second request while one is open would stack a second
        browser and a second topmost drop/restore pair.
        """
        if not self._browse_lock.acquire(blocking=False):
            return
        try:
            self._browse_library_inner()
        finally:
            self._browse_lock.release()

    def _browse_library_inner(self) -> None:
        # Browsing keeps everything playing — it must NOT enter OmniPause.  The
        # old flow paused the whole session for the browse, and picking a video
        # resumed only Nau, stranding the satellites + voice frozen ("we're in
        # omnipause").  All the browser actually needs is to not be buried under
        # the always-on-top windows, so drop the topmost bands for its duration
        # and restore them after — playback and voice are never touched.  Under
        # OmniPause the bands are already down and must stay down (restoring
        # them would strand windows on top mid-pause), so only manage them when
        # not paused.
        #
        # The hotkeys go the same way, and for the browser's sake rather than
        # its own: they are global and they *consume* the press, so the arrows
        # would move the portrait satellite instead of the selection and every
        # letter would fire a command instead of typing ahead through an
        # alphabetical grid.  Suspending hands the keyboard to the browser for
        # the browse; under OmniPause they are already suspended and the pause
        # owns that hold, so it is left to release it.
        manage_session = not self.state.omni_paused

        if manage_session:
            self.windows.remove_all_topmost()
            self.ahk_cmd_file.write_text("suspend_hotkeys", encoding="utf-8")

        try:
            # Over Nau's own rect: the pick plays there, so the browse stands
            # where the video will, and covers nothing else on either monitor.
            nau_hwnd = self.windows.hwnd("nau")
            selected = browse_library(
                self.manifest_path,
                self.config.python_exe,
                over=window_rect(nau_hwnd) if nau_hwnd else None,
                runner=self._run_browser,
            )
            if selected:
                # Nau owns the main player; play the pick there, paired with
                # its funscript when one exists at the mirrored path.
                mirrored = build_mirrored_funscript_path(selected)
                if mirrored and Path(mirrored).exists():
                    command = f"PLAY_FILE {selected}\t{mirrored}"
                else:
                    command = f"PLAY_FILE {selected}"
                append_command(self.config.nau_cmd_file, command)
        finally:
            if manage_session:
                self.windows.restore_all_topmost(
            self.state.main_mode, self.state.satellites_mode)
                self.ahk_cmd_file.write_text("unsuspend_hotkeys", encoding="utf-8")

    def run(self) -> None:
        """Main loop — call from a background thread."""
        # Log the topmost state once the loop starts so a "windows not all on top
        # after launch" report can be pinned to the exact window that missed its
        # startup promotion.
        self._log_topmost_state("startup")
        last_wall = time.time()
        while not self._stop.is_set():
            now = time.time()
            gap = detect_sleep_gap(last_wall, now)
            if gap is not None:
                logger.warning(
                    "Dispatch loop resumed after %.0fs stall — likely system "
                    "sleep/standby; AHK hotkeys may be dead until Fun Time is restarted",
                    gap,
                )
            last_wall = now
            try:
                self.tick()
            except Exception:
                logger.exception("Dispatch loop error")
            self._stop.wait(0.05)

    def _run_browser(self, command, **kwargs) -> None:
        """Run the browser, holding it while it is up so :meth:`stop` can end it."""
        process = subprocess.Popen(command, **kwargs)
        self._browser_process = process
        try:
            process.wait()
        finally:
            self._browser_process = None

    def stop(self) -> None:
        self._stop.set()
        # A browse still on screen is the session's window, so it goes with the
        # session — otherwise quitting Fun Time leaves the grid up over an empty
        # desktop, owned by nothing that is still running.
        browsing = self._browser_process
        if browsing is not None:
            browsing.terminate()


def build_bridge_config_from_manifest(
    manifest: configparser.ConfigParser,
) -> BridgeConfig:
    """Build a BridgeConfig from the windows bridge manifest INI."""
    commands = manifest["commands"]
    return BridgeConfig(
        portrait_cmd_file=Path(commands["portrait_cmd_file"]),
        portrait_paused_file=Path(commands["portrait_paused_file"]),
        portrait_status_file=Path(commands["portrait_status_file"]),
        portrait_playlist_file=Path(commands["portrait_playlist_file"]),
        landscape_cmd_file=Path(commands["landscape_cmd_file"]),
        landscape_paused_file=Path(commands["landscape_paused_file"]),
        landscape_status_file=Path(commands["landscape_status_file"]),
        landscape_playlist_file=Path(commands["landscape_playlist_file"]),
        favs_file=Path(manifest["media"]["favs_file"]),
        weird_dir=Path(manifest["media"]["weird_dir"]),
        state_dir=Path(manifest["commands"]["dashboard_state_file"]).parent,
        main_sources=manifest["media"]["nau_library_sources"],
        python_exe=manifest["executables"]["python_exe"],
        portrait_sources=manifest["media"]["portrait_dirs"],
        landscape_sources=manifest["media"]["landscape_dirs"],
        genau_mode_file=Path(manifest["commands"]["genau_mode_file"]),
        genau_cmd_file=Path(manifest["commands"]["genau_cmd_file"]),
        genau_paused_file=Path(manifest["commands"]["genau_paused_file"]),
        audio_paused_file=Path(manifest["commands"]["audio_paused_file"]),
        audio_volume_file=Path(manifest["commands"]["audio_volume_file"]),
        nau_cmd_file=Path(manifest["commands"]["nau_cmd_file"]),
        nau_paused_file=Path(manifest["commands"]["nau_paused_file"]),
        nau_status_file=Path(manifest["commands"]["nau_status_file"]),
        nau_notice_file=Path(manifest["commands"]["nau_status_file"]).with_name("nau_notice.txt"),
        dashboard_state_file=Path(manifest["commands"]["dashboard_state_file"]),
        broker_cmd_file=Path(manifest["commands"]["broker_cmd_file"]),
        broker_heartbeat_file=Path(manifest["commands"]["broker_heartbeat_file"]),
        broker_state_dir=Path(v) if (v := manifest["commands"].get("broker_state_dir", "").strip()) else None,
        broker_tray_launcher=Path(v) if (v := manifest["commands"].get("broker_tray_launcher", "").strip()) else None,
        regen_media_root=Path(v) if (v := manifest.get("regen", "media_root", fallback="").strip()) else None,
        regen_metadata_root=Path(v) if (v := manifest.get("regen", "metadata_root", fallback="").strip()) else None,
        regen_generate_video_url=manifest.get("regen", "generate_video_url", fallback="https://example.com/video"),
        regen_generate_image_url=manifest.get("regen", "generate_image_url", fallback="https://example.com/create"),
        origenerator_enabled=bool(
            manifest.get("runtime", "origenerator_dir", fallback="").strip()),
        origenerator_cmd_file=Path(v) if (v := manifest["commands"].get("origenerator_cmd_file", "").strip()) else None,
        origenerator_paused_file=Path(v) if (v := manifest["commands"].get("origenerator_paused_file", "").strip()) else None,
    )
