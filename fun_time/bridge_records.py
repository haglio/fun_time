"""The records the command dispatcher trades in — :class:`BridgeConfig` in,
:class:`WindowOp` out — below the dispatcher and its handler modules."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .config import RegenConfig
from .event_log import FAVORITE, NOTICE, SOURCE_SYSTEM
from .player_status import genau_enabled_path

# A notice that reports a command had no effect ("No other seeds") is logged at
# ERROR so the log panel and the on-player flash render it red, not white — the
# user asked to tell a command that did something from one that hit a dead end at
# a glance.
FAILED_NOTICE_LEVEL = logging.ERROR

# The other end of the same trick: a notice about the favorites — locking a clip
# into them, taking one back out, turning their filter on — is logged a level
# above NOTICE so it flashes green, which is what green means everywhere in this
# app.  Everything else a command announces is a plain white NOTICE.
FAVORITE_NOTICE_LEVEL = FAVORITE


@dataclass
class BridgeConfig:
    # Each satellite (2=portrait, 3=landscape) is a native mpv-backed player
    # driven through a file quartet — a command file it drains verbs from, a
    # paused flag it obeys, a status file it publishes, and the playlist file it
    # plays.  See :mod:`fun_time.satellite_control`.
    portrait_cmd_file: Path
    portrait_paused_file: Path
    portrait_status_file: Path
    portrait_playlist_file: Path
    landscape_cmd_file: Path
    landscape_paused_file: Path
    landscape_status_file: Path
    landscape_playlist_file: Path
    favs_file: Path
    weird_dir: Path
    state_dir: Path
    main_sources: str
    portrait_sources: str
    landscape_sources: str
    genau_mode_file: Path
    genau_cmd_file: Path
    genau_paused_file: Path
    audio_paused_file: Path
    audio_volume_file: Path
    nau_cmd_file: Path
    nau_paused_file: Path
    nau_status_file: Path
    dashboard_state_file: Path
    # Where Nau publishes its one-shot notices (a clip jump with nowhere to go).
    nau_notice_file: Path | None = None
    # Our own interpreter — what the library browser is launched with, since the
    # bridge process has no Qt event loop to host that window in.
    python_exe: str = ""
    broker_cmd_file: Path | None = None
    broker_heartbeat_file: Path | None = None
    broker_tray_launcher: Path | None = None
    # The hosted Origenerator's channel (see fun_time.satellites_mode).  Enabled
    # only when the config names an origenerator checkout; without one the
    # satellites have no origenerator mode and its commands report a dead end.
    origenerator_enabled: bool = False
    origenerator_cmd_file: Path | None = None
    origenerator_paused_file: Path | None = None
    # Where the broker keeps the rest of its channel.  Unset it falls back to
    # ``state_dir``, which is what the two are for every session that runs from
    # the primary checkout; a branch session moves ``state_dir`` into its worktree
    # and this stays on the main player, because the broker is still the machine's one
    # broker.  See :attr:`fun_time.config.PathsConfig.broker_state_dir`.
    broker_state_dir: Path | None = None
    regen_media_root: Path | None = None
    regen_metadata_root: Path | None = None
    regen_generate_video_url: str = "https://example.com/video"
    regen_generate_image_url: str = "https://example.com/create"

    def satellite_cmd_file(self, which: int) -> Path:
        return self.portrait_cmd_file if which == 2 else self.landscape_cmd_file

    def satellite_status_file(self, which: int) -> Path:
        return self.portrait_status_file if which == 2 else self.landscape_status_file

    def satellite_playlist_file(self, which: int) -> Path:
        return self.portrait_playlist_file if which == 2 else self.landscape_playlist_file

    @property
    def broker_state(self) -> Path:
        """The directory the broker's files live in, defaulted to our own."""
        return self.broker_state_dir or self.state_dir

    @property
    def genau_enabled_file(self) -> Path:
        """Our switch for whether the broker may hand the OSR2 to Genau."""
        return genau_enabled_path(self.broker_state)

    @property
    def osr2_serial_rx_file(self) -> Path:
        """When the OSR2 last spoke, as the broker last stamped it."""
        return self.broker_state / "osr2_serial_rx.txt"

    @property
    def osr2_serial_tx_file(self) -> Path:
        """When a driver last spoke TO the OSR2.  The device only replies to
        traffic, so through a quiet stretch (an OmniPause, a handoff buffer) the
        RX stamp alone goes stale on a device that is on and in use — this one
        says somebody is still driving it."""
        return self.broker_state / "osr2_serial_tx.txt"

    @property
    def regen(self) -> RegenConfig:
        """The four Provider settings, in the shape the regenerate code expects."""
        return RegenConfig(
            generate_video_url=self.regen_generate_video_url,
            generate_image_url=self.regen_generate_image_url,
            media_root=self.regen_media_root,
            metadata_root=self.regen_metadata_root,
        )


@dataclass(frozen=True)
class WindowOp:
    op: str
    # The op's one payload: a role name, an RFB URL, or a notice's message.
    key: str = ""
    # Which window a ``notice`` op is about, so the log panel can filter it.
    source: str = SOURCE_SYSTEM
    # The log level a ``notice`` op is logged at — NOTICE (white) for a normal
    # confirmation, FAVORITE (green) for one about the favorites or a funscript,
    # ERROR (red) for a command that hit a dead end.
    level: int = NOTICE
