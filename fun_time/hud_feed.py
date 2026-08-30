"""What the players draw for themselves: the lock HUDs and the main console.

The dispatch loop holds the state these are drawn from (locks, filters, loops)
and already ticks, so it is what feeds them — but building a panel is a question
about the session's config and its state, not about the loop, and this is where
that question is answered.
"""
from __future__ import annotations

from pathlib import Path

from .command_dispatch import MAIN_SIDE, BridgeConfig, side_name
from .shared_state import BridgeState
from .player_status import (
    genau_status_path,
    is_broker_heartbeat_fresh,
    is_osr2_device_on,
    read_genau_status,
    read_nau_status,
)
from .hud_transport import HudPublisher
from .lock_hud import SideInputs, build_panels, origenerator_mode_panel
from .modes import is_favorite_path, read_favs_content
from .nau_console import console_payload
from .runtime_flow import read_flag_file
from .satellite_control import read_satellite_status
from .satellites_mode import origenerator_shows

# The satellites play ~5 s clips, so the HUD map has to track the current clip
# almost the instant it changes — but not at the loop's own 20 Hz.  Building a
# panel is index lookups plus a stat per thumbnail, and the publisher skips the
# write entirely when the panel is unchanged, so an idle tick is nearly free.
PUBLISH_INTERVAL_S = 0.15


class HudFeed:
    """Builds both satellites' panels and the console, and publishes them."""

    def __init__(self, *, config: BridgeConfig, publisher: HudPublisher | None) -> None:
        self.config = config
        self.publisher = publisher
        self._last_publish = 0.0
        # The favorites list, and the stat that says whether it has moved (see
        # _favs_content) — every publish asks whether the clip on screen is on it.
        self._favs_text = ""
        self._favs_stamp: tuple[int, int] | None = None
        # The clip each satellite last named, so a status read that loses the
        # race with the player's own republish does not blank its map.
        self._last_satellite_clip: dict[str, str] = {}

    def publish_due(self, state: BridgeState, *, now: float) -> None:
        """Publish, if the cadence says it is time."""
        if now - self._last_publish < PUBLISH_INTERVAL_S:
            return
        self._last_publish = now
        self.publish(state)

    def publish(self, state: BridgeState) -> None:
        """Rebuild both satellites' HUD panels and publish the ones that changed.

        Runs under OmniPause too: playback is frozen, but the map stays up so the
        user can still see — and click — what each satellite is holding.
        """
        if self.publisher is None:
            return
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
        self.publisher.publish("portrait", portrait)
        self.publisher.publish("landscape", landscape)
        # The main console: the controls the dashboard used to hold for
        # whichever player owns the slot, what has the OSR2, whether the broker is
        # up, and which player a bare command reaches — none of which the player
        # can see for itself.
        nau = read_nau_status(self.config.nau_status_file)
        self.publisher.publish_payload("nau", console_payload(
            mode=state.main_mode,
            active=state.active_side == MAIN_SIDE,
            f_mode=state.main_f_mode,
            latest=state.main_latest,
            genau_latest=state.genau_latest,
            osr2_mode=self.osr2_mode(),
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

    def osr2_mode(self) -> str:
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
