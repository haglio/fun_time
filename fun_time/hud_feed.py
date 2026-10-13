"""What the Funestras draw for themselves: the lock HUDs and the main console.

The dispatch loop holds the state these are drawn from (locks, filters, loops)
and already ticks, so it is what feeds them — but building a panel is a question
about the session's config and its state, not about the loop, and this is where
that question is answered.
"""
from __future__ import annotations

from pathlib import Path

from funestra.console import aim_row, console_text
from funestra.drive_readout import read_drive
from funestra.satellite_hud import HudModel, hud_text, parse_hud

from main_funestra.controls import longer_than_a_step

from .bridge_records import BridgeConfig
from .command_dispatch import (
    genau_flick_shapes,
    hosts,
    main_funestra_at_defaults,
    main_video_shapes,
    satellite_at_defaults,
)
from .console_buttons import osr2_controls
from .crown import Crown
from .funestra_status import (
    is_broker_heartbeat_fresh,
    is_osr2_device_on,
    read_genau_status,
    read_main_funestra_status,
)
from .hud_transport import HudPublisher, hosted_model
from .lock_hud import SatelliteInputs, build_satellite_panel
from .main_funestra_console import MainSlotInputs, console_model, device_word
from .media_renditions import renditions
from .modes import is_favorite_path, read_favs_content
from .osr2_section import DeviceBlock, funestra_with_the_osr2
from .room_funestras import RoomFunestra
from .runtime_flow import read_flag_file
from .satellite_control import SatelliteStatus, read_satellite_status
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
        self._last_satellite_status: dict[str, SatelliteStatus] = {}
        self._hosted_panels: dict[RoomFunestra, HudModel | None] = {}

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
        genau = read_genau_status(self.config.genau_status_file)
        broker = (is_broker_heartbeat_fresh(self.config.broker_heartbeat_file)
                  if self.config.broker_heartbeat_file else False)
        osr2_mode = self.osr2_mode()
        with_the_osr2 = funestra_with_the_osr2(state)

        def device(funestra: RoomFunestra) -> DeviceBlock:
            if funestra is not with_the_osr2:
                return DeviceBlock.offered_to(funestra)
            scripted = read_satellite_status(self.config.satellite(funestra).status_file)
            return DeviceBlock(
                rows=(aim_row(learned=genau.learned_active, shape=genau.shape,
                              control=state.osr2_control),),
                osr2=device_word(osr2_mode, scripted.funscript_driving),
                control=state.osr2_control,
                controls=osr2_controls(broker=broker),
                max_intensity=state.max_intensity)

        def satellite(funestra: RoomFunestra) -> SatelliteInputs:
            name = funestra.label
            status = self._satellite_status(name, self.config.satellite(funestra).status_file)
            current = status.video
            values = state.satellite(funestra)
            return SatelliteInputs(
                funestra=name, sources=self.config.satellite(funestra).sources,
                current=current, locked=values.locked,
                hud_corner=values.hud_corner, hud_edge=values.hud_edge,
                hud_minimized=values.hud_minimized,
                filter_query=values.filter,
                loop_axis=values.loop,
                map_anchor=values.map_anchor,
                widen_clip=values.widen_clip,
                nav_anchor=values.nav_anchor,
                latest=values.latest,
                favorites_filter=values.favorites_filter,
                is_favorite=is_favorite_path(current, favs),
                nothing_to_reset=satellite_at_defaults(values, status),
                has_other_versions=bool(renditions(current, self.config.regen_media_root)),
                longer_than_a_step=longer_than_a_step(status.duration_ms),
                # None for a session hosting no Origenerator — the HUD then draws
                # no mode pair at all, rather than a switch that can only dead-end.
                mode=values.mode if self.config.origenerator_enabled else None,
            )

        for funestra in RoomFunestra.SATELLITES:
            if hosts(funestra, state, self.config):
                self.publisher.publish_text(funestra.label, hud_text(hosted_model(
                    funestra.label, self._hosted_panel(funestra),
                    active=state.active_funestra == funestra,
                    origenerator_ready=state.origenerator_ready,
                    in_vr=self.config.vr_main_funestra,
                    crowned=state.crowned == funestra.label, device=device(funestra),
                    hud_corner=state.satellite(funestra).hud_corner,
                    hud_edge=state.satellite(funestra).hud_edge,
                    hud_minimized=state.satellite(funestra).hud_minimized)))
                continue
            self.publisher.publish(funestra.label, build_satellite_panel(
                satellite(funestra), self.config.regen_metadata_root,
                RoomFunestra.label_of(state.active_funestra),
                origenerator_ready=state.origenerator_ready,
                in_vr=self.config.vr_main_funestra, crowned=state.crowned,
            ), device(funestra))
        # The main console: the controls for whichever Funestra owns the slot,
        # what has the OSR2, whether the broker is up, and which Funestra a bare
        # command reaches -- none of which the Funestra can see for itself.
        main_funestra = read_main_funestra_status(self.config.main_funestra_status_file)
        main_shapes = main_video_shapes(state, self.config)
        genau_shapes = genau_flick_shapes(state, self.config)
        self.publisher.publish_text("main_player", console_text(console_model(MainSlotInputs(
            main_mode=state.main_mode,
            hud_corner=state.main_hud_corner,
            hud_edge=state.main_hud_edge,
            hud_minimized=state.main_hud_minimized,
            active=state.active_funestra == RoomFunestra.MAIN,
            scripted_filter=state.main_scripted_filter,
            latest=state.main_latest,
            genau_latest=state.genau_latest,
            plays_vr=main_shapes.plays_vr if main_shapes.offered else None,
            plays_flat=main_shapes.plays_flat if main_shapes.offered else None,
            length_mode=state.main_length_mode,
            genau_plays_vr=genau_shapes.plays_vr if genau_shapes.offered else None,
            genau_plays_flat=genau_shapes.plays_flat if genau_shapes.offered else None,
            osr2_mode=osr2_mode,
            osr2_control=state.osr2_control,
            broker=broker,
            # The console's buttons are lit and named from what each Funestra
            # published, since the Funestra drawing it is not always their subject.
            main_funestra=main_funestra,
            genau=genau,
            genau_pace_s=self._genau_pace_s(),
            nothing_to_reset=main_funestra_at_defaults(state, self.config, main_funestra),
            in_vr=self.config.vr_main_funestra,
            crowned=state.crowned is Crown.MAIN,
            has_osr2=with_the_osr2 is RoomFunestra.MAIN,
            max_intensity=state.max_intensity,
        ))))

    def _hosted_panel(self, funestra: RoomFunestra) -> HudModel | None:
        """The hosted app's panel for *Funestra*'s side, or None; one it is
        replacing this instant leaves the panel read before it standing."""
        path = self.config.satellite(funestra).origenerator_hud_file
        if path is None:
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        except OSError:
            return self._hosted_panels.get(funestra)
        panel = self._hosted_panels[funestra] = parse_hud(text)
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

    def _satellite_status(self, funestra: str, status_file: Path) -> SatelliteStatus:
        """What *Funestra* last said about itself, held over a read that comes back
        blank.

        A satellite always has a clip — it cannot discard its way to an empty
        playlist — so once one has named a clip, a blank status means the read
        lost a race with the Funestra's own republish, not that the Funestra has
        nothing.  Believing the blank builds an empty panel, and publishing that
        blanks the map on screen until the next tick puts it back.  Before a
        satellite's first status there is nothing to hold, and an empty map is
        the truth.
        """
        status = read_satellite_status(status_file)
        if status.video:
            self._last_satellite_status[funestra] = status
            return status
        return self._last_satellite_status.get(funestra, status)

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
