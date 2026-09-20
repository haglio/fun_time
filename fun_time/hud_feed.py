"""What the players draw for themselves: the lock HUDs and the main console.

The dispatch loop holds the state these are drawn from (locks, filters, loops)
and already ticks, so it is what feeds them — but building a panel is a question
about the session's config and its state, not about the loop, and this is where
that question is answered.
"""
from __future__ import annotations

from pathlib import Path

from player_core.console import console_text
from player_core.drive_readout import read_drive
from player_core.satellite_hud import HudModel, hud_text, parse_hud

from .bridge_records import BridgeConfig
from .command_dispatch import main_player_at_defaults, satellite_at_defaults
from .hud_transport import HudPublisher, hosted_model
from .lock_hud import SatelliteInputs, build_panels
from .main_player_console import console_model
from .media_renditions import renditions
from .modes import is_favorite_path, read_favs_content, source_roots
from .player_status import (
    genau_status_path,
    is_broker_heartbeat_fresh,
    is_osr2_device_on,
    read_genau_status,
    read_main_player_status,
)
from .players import Player
from .runtime_flow import read_flag_file
from .satellite_control import read_satellite_status
from .satellites_mode import origenerator_shows
from .shared_state import BridgeState

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
        self._hosted_panels: dict[Player, HudModel | None] = {}

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

        def satellite(name: str, player: Player, *, sources: str, status_file: Path) -> SatelliteInputs:
            current = self._satellite_clip(name, status_file)
            values = state.satellite(player)
            return SatelliteInputs(
                player=name, sources=sources, current=current, locked=values.locked,
                filter_query=values.filter,
                loop_axis=values.loop,
                map_anchor=values.map_anchor,
                widen_clip=values.widen_clip,
                nav_anchor=values.nav_anchor,
                latest=values.latest,
                favorites_filter=values.favorites_filter,
                is_favorite=is_favorite_path(current, favs),
                nothing_to_reset=satellite_at_defaults(values),
                has_other_versions=bool(renditions(current, self.config.regen_media_root)),
            )

        if self.config.origenerator_enabled and origenerator_shows(state.satellites_mode):
            for player in Player.SATELLITES:
                self.publisher.publish_text(player.label, hud_text(hosted_model(
                    player.label, self._hosted_panel(player),
                    active=state.active_player == player,
                    origenerator_ready=state.origenerator_ready)))
        else:
            portrait, landscape = build_panels(
                satellite("portrait", 2, sources=self.config.portrait_sources,
                     status_file=self.config.portrait_status_file),
                satellite("landscape", 3, sources=self.config.landscape_sources,
                     status_file=self.config.landscape_status_file),
                metadata_root=self.config.regen_metadata_root,
                active_player=Player.label_of(state.active_player),
                # None for a session hosting no Origenerator — the HUDs then draw
                # no mode pair at all, rather than a switch that can only dead-end.
                satellites_mode=(state.satellites_mode
                                 if self.config.origenerator_enabled else None),
                origenerator_ready=state.origenerator_ready,
            )
            self.publisher.publish("portrait", portrait)
            self.publisher.publish("landscape", landscape)
        # The main console: the controls the dashboard used to hold for
        # whichever player owns the slot, what has the OSR2, whether the broker is
        # up, and which player a bare command reaches — none of which the player
        # can see for itself.
        main_player = read_main_player_status(self.config.main_player_status_file)
        shapes_offered = bool(source_roots(self.config.vr_library_dirs))
        self.publisher.publish_text("main_player", console_text(console_model(
            main_mode=state.main_mode,
            active=state.active_player == Player.MAIN,
            scripted_filter=state.main_scripted_filter,
            latest=state.main_latest,
            genau_latest=state.genau_latest,
            # None where the rotation holds one shape: the pair is the headset's.
            plays_vr=state.main_plays_vr if shapes_offered else None,
            plays_flat=state.main_plays_flat if shapes_offered else None,
            osr2_mode=self.osr2_mode(),
            osr2_control=state.osr2_control,
            broker=is_broker_heartbeat_fresh(self.config.broker_heartbeat_file)
            if self.config.broker_heartbeat_file else False,
            # The console's buttons are lit and named from what each player
            # published, since the player drawing it is not always their subject.
            main_player=main_player,
            genau=read_genau_status(genau_status_path(self.config.state_dir)),
            genau_pace_s=self._genau_pace_s(),
            nothing_to_reset=main_player_at_defaults(state, self.config, main_player),
        )))

    def _hosted_panel(self, player: Player) -> HudModel | None:
        """The hosted app's panel for *player*'s side, or None; one it is
        replacing this instant leaves the panel read before it standing."""
        path = self.config.satellite(player).origenerator_hud_file
        if path is None:
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        except OSError:
            return self._hosted_panels.get(player)
        panel = self._hosted_panels[player] = parse_hud(text)
        return panel

    def _genau_pace_s(self) -> int:
        drive = read_drive(self.config.genau_drive_file)
        return drive.advance_interval if drive is not None else 0

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

    def _satellite_clip(self, player: str, status_file: Path) -> str:
        """The clip *player* is showing, holding the last one it named if the read
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
            self._last_satellite_clip[player] = video
            return video
        return self._last_satellite_clip.get(player, "")

    def osr2_mode(self) -> str:
        """What the device is doing: "off" when nothing is on the wire at all,
        "auto" while Genau has claimed it, "controlled" otherwise.

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
        return "auto" if read_flag_file(self.config.broker_mode_file, False) else "controlled"
