"""The launch manifest: the one file every child of a session reads back.

``state/windows_bridge_launch.ini`` is written once per launch and read by the
AHK hotkey shell, the dashboard, the dispatch loop, the satellites and the VR
orchestrator, with ``optionxform = str`` making the exact spelling of every key
load-bearing.  Both halves of that schema live here — :func:`build_windows_bridge_manifest`
writes it and :class:`LaunchManifest` reads it — so a key cannot be respelled on
one side alone.
"""
from __future__ import annotations

import configparser
import os
from dataclasses import MISSING, dataclass, fields
from pathlib import Path

from .config import LayoutConfig, RegenConfig
from .hud_transport import HUD_FILENAME
from .nau_console import nau_console_path

WINDOWS_BRIDGE_MANIFEST_FILENAME = "windows_bridge_launch.ini"


def build_windows_bridge_manifest(config) -> dict[str, dict[str, str]]:
    layout = config.layout
    dashboard_enabled = os.environ.get("FUN_TIME_DISABLE_DASHBOARD") != "1"
    return {
        "runtime": {
            "config_path": str(config.config_path),
            "windows_bridge_log_file": str(config.log_file("windows_bridge")),
            "genau_config_path": str(config.paths.genau_config_path or config.config_path),
            # Where Genau and Nau are started from.  Empty means "wherever we
            # are", which resolves them through their venv's editable install —
            # the primary genau checkout.  Named, a worktree of that repo can be
            # run instead, so a branch of it is judged before it lands.
            "genau_project_dirs": os.pathsep.join(
                str(path) for path in config.paths.genau_project_dirs),
            # The Origenerator checkout the session hosts, or "" for a session
            # with no origenerator mode at all (see fun_time.satellites_mode).
            "origenerator_dir": str(config.paths.origenerator_dir or ""),
        },
        "executables": {
            # Two interpreters: ours runs everything this repo ships (the
            # dashboard, the audio companion, the satellite players), and
            # genau's runs the apps that live in ../genau (Genau and Nau).
            "python_exe": str(config.paths.python_exe),
            "genau_python_exe": str(config.paths.genau_python_exe or config.paths.python_exe),
            # Origenerator has no venv; its deps live in a system install its
            # own launcher would find, so a session must be told which python
            # that is.  Empty with no origenerator configured.
            "origenerator_python_exe": str(config.paths.origenerator_python_exe or ""),
        },
        "media": {
            "nau_library_sources": "|".join(str(path) for path in config.paths.nau_library_dirs),
            "portrait_dirs": "|".join(str(path) for path in config.paths.portrait_dirs),
            "landscape_dirs": "|".join(str(path) for path in config.paths.landscape_dirs),
            "weird_dir": str(config.paths.weird_dir),
            "favs_file": str(config.paths.favs_file),
            "genau_clips": str(config.paths.clips_dir),
            "genau_audio": str(config.paths.audio_dir),
        },
        "modules": {
            "genau_module": "genau",
            "nau_module": "nau",
            "satellite_module": "satellite",
            "audio_module": "fun_time.audio_companion_app",
            "dashboard_module": "fun_time.dashboard_app",
        },
        "commands": {
            "genau_mode_file": str(config.genau_mode_file),
            "genau_cmd_file": str(config.genau_cmd_file),
            "genau_paused_file": str(config.genau_paused_file),
            "nau_cmd_file": str(config.nau_cmd_file),
            "nau_paused_file": str(config.nau_paused_file),
            "nau_status_file": str(config.nau_status_file),
            "nau_console_file": str(nau_console_path(config.paths.state_dir)),
            "nau_playlist_file": str(config.nau_playlist_file),
            "portrait_cmd_file": str(config.paths.state_dir / "portrait_cmd.txt"),
            "portrait_paused_file": str(config.paths.state_dir / "portrait_paused.txt"),
            "portrait_status_file": str(config.paths.state_dir / "portrait_status.txt"),
            "portrait_playlist_file": str(config.paths.state_dir / "portrait_playlist.tsv"),
            "portrait_hud_file": str(config.paths.state_dir / HUD_FILENAME["portrait"]),
            "landscape_cmd_file": str(config.paths.state_dir / "landscape_cmd.txt"),
            "landscape_paused_file": str(config.paths.state_dir / "landscape_paused.txt"),
            "landscape_status_file": str(config.paths.state_dir / "landscape_status.txt"),
            "landscape_playlist_file": str(config.paths.state_dir / "landscape_playlist.tsv"),
            "landscape_hud_file": str(config.paths.state_dir / HUD_FILENAME["landscape"]),
            "broker_cmd_file": str(config.broker_cmd_file),
            "broker_heartbeat_file": str(config.broker_heartbeat_file),
            # The broker's own directory, so a child needing a broker file we have
            # not named here resolves it against the broker rather than against the
            # session — which is what put the console's broker and OSR2 lights on a
            # branch session's empty state dir.
            "broker_state_dir": str(config.paths.broker_state_dir),
            "broker_tray_launcher": str(config.paths.broker_tray_launcher or ""),
            "audio_paused_file": str(config.audio_paused_file),
            "audio_volume_file": str(config.audio_volume_file),
            "dashboard_state_file": str(config.paths.state_dir / "dashboard_state.ini"),
            "dashboard_cmd_file": str(config.paths.state_dir / "dashboard_cmd.txt"),
            "origenerator_cmd_file": str(config.origenerator_cmd_file),
            "origenerator_paused_file": str(config.origenerator_paused_file),
            "origenerator_status_file": str(config.origenerator_status_file),
        },
        "dashboard": {
            "enabled": "1" if dashboard_enabled else "0",
        },
        "loopback": {
            "port": str(config.loopback_port),
        },
        "layout": {
            "primary_monitor": str(layout.primary_monitor),
            "secondary_monitor": str(layout.secondary_monitor),
            "main_top_ratio": str(layout.main_top_ratio),
            "landscape_width_ratio": str(layout.landscape_width_ratio),
        },
        "random_favs_browser": {
            "enabled": "1" if config.random_favs_browser.enabled else "0",
            "shortcut_path": str(config.random_favs_browser.shortcut_path),
            "manifest_file": str(config.random_favs_browser_manifest_file),
        },
        "regen": {
            "generate_video_url": config.regen.generate_video_url,
            "generate_image_url": config.regen.generate_image_url,
            "media_root": str(config.regen.media_root or ""),
            "metadata_root": str(config.regen.metadata_root or ""),
        },
    }


def write_manifest_data(data: dict[str, dict[str, str]], destination: Path) -> Path:
    """Write a manifest dict as the INI every child process reads back.

    Split from :func:`write_windows_bridge_manifest` so a variant session
    (FunTimeVR) can amend the built dict before it hits disk.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_dict(data)
    with destination.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return destination


def write_windows_bridge_manifest(config, destination: Path | None = None) -> Path:
    manifest_path = destination or (config.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME)
    return write_manifest_data(build_windows_bridge_manifest(config), manifest_path)


class ManifestKeyMissing(LookupError):  # not KeyError: str() must be the message, not its repr()
    """A manifest that does not carry a key the session needs.

    Raised naming the key AND the file, because the interesting question when
    this happens is always which manifest — a session's own, a branch session's,
    or one a stale process is still holding.
    """


@dataclass(frozen=True)
class RuntimePaths:
    """[runtime]: the config this session was built from, and the checkouts it runs."""

    config_path: str
    windows_bridge_log_file: str
    genau_config_path: str
    # Where Genau and Nau are started from.  Empty means "wherever we are".
    genau_project_dirs: str = ""
    # The Origenerator checkout the session hosts, or "" for a session with
    # no origenerator mode at all.
    origenerator_dir: str = ""


@dataclass(frozen=True)
class Executables:
    """[executables]: the interpreters, one per family of children."""

    python_exe: str
    genau_python_exe: str
    origenerator_python_exe: str = ""


@dataclass(frozen=True)
class MediaSources:
    """[media]: the libraries and folders the players draw from."""

    nau_library_sources: str
    portrait_dirs: str
    landscape_dirs: str
    weird_dir: str
    favs_file: str
    genau_clips: str
    genau_audio: str


@dataclass(frozen=True)
class ChildModules:
    """[modules]: what each child is launched as (``python -m <module>``)."""

    genau_module: str
    nau_module: str
    satellite_module: str
    audio_module: str
    dashboard_module: str


@dataclass(frozen=True)
class CommandFiles:
    """[commands]: every file channel and flag a child of this session reads."""

    genau_mode_file: str
    genau_cmd_file: str
    genau_paused_file: str
    nau_cmd_file: str
    nau_paused_file: str
    nau_status_file: str
    nau_console_file: str
    nau_playlist_file: str
    portrait_cmd_file: str
    portrait_paused_file: str
    portrait_status_file: str
    portrait_playlist_file: str
    portrait_hud_file: str
    landscape_cmd_file: str
    landscape_paused_file: str
    landscape_status_file: str
    landscape_playlist_file: str
    landscape_hud_file: str
    broker_cmd_file: str
    broker_heartbeat_file: str
    audio_paused_file: str
    audio_volume_file: str
    dashboard_state_file: str
    dashboard_cmd_file: str
    origenerator_status_file: str
    # The five a reader has always defaulted rather than demanded, kept
    # defaulted so this parse refuses nothing today's readers accept.
    broker_state_dir: str = ""
    broker_tray_launcher: str = ""
    origenerator_cmd_file: str = ""
    origenerator_paused_file: str = ""

    def side_file(self, side: str, kind: str) -> str:
        """One satellite side's file of a given kind, asked for by side rather
        than spelled out — ``side_file("portrait", "hud")`` is
        ``portrait_hud_file``.  Both the HUD publisher and the VR player build
        their sides in a loop and have nowhere to write the key by hand."""
        return getattr(self, f"{side}_{kind}_file")


@dataclass(frozen=True)
class RandomFavsBrowserSettings:
    """[random_favs_browser]: the browser the session opens, if it opens one."""

    enabled: bool
    shortcut_path: str
    manifest_file: str


@dataclass(frozen=True)
class RegenSettings:
    """[regen]: where a generated video goes and which page makes one."""

    generate_video_url: str = RegenConfig.generate_video_url
    generate_image_url: str = RegenConfig.generate_image_url
    media_root: str = ""
    metadata_root: str = ""


_SECTION_RECORDS: dict[str, tuple[str, type]] = {
    "runtime": ("runtime", RuntimePaths),
    "executables": ("executables", Executables),
    "media": ("media", MediaSources),
    "modules": ("modules", ChildModules),
    "commands": ("commands", CommandFiles),
    "regen": ("regen", RegenSettings),
}


@dataclass(frozen=True)
class LaunchManifest:
    """One session's manifest, read once and asked for by name after that.

    Values are the strings the file carries, unconverted — what a child is
    handed on its command line has to be byte-for-byte what the writer put in
    the file — except the four that were never strings to begin with: the two
    enabled flags, the loopback port and the layout numbers.
    """

    runtime: RuntimePaths
    executables: Executables
    media: MediaSources
    modules: ChildModules
    commands: CommandFiles
    dashboard_enabled: bool
    loopback_port: int
    layout: LayoutConfig
    random_favs_browser: RandomFavsBrowserSettings
    regen: RegenSettings

    @classmethod
    def read(cls, path: str | Path) -> LaunchManifest:
        """Parse one manifest, raising :class:`ManifestKeyMissing` on a gap.

        Unknown sections and keys are ignored: FunTimeVR amends the built dict
        with a ``[vr]`` section of its own before writing it, and a manifest is
        allowed to carry more than this session reads.
        """
        path = Path(path)
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(str(path), encoding="utf-8")

        def record(section: str, kind: type):
            values = parser[section] if parser.has_section(section) else {}
            missing = [f.name for f in fields(kind)
                       if f.name not in values and f.default is MISSING]
            if missing:
                raise ManifestKeyMissing(
                    f"[{section}] {', '.join(missing)} missing from {path}")
            return kind(**{f.name: values[f.name] for f in fields(kind) if f.name in values})

        def flag(section: str, key: str) -> str:
            if not parser.has_option(section, key):
                raise ManifestKeyMissing(f"[{section}] {key} missing from {path}")
            return parser[section][key]

        return cls(
            **{attr: record(section, kind)
               for section, (attr, kind) in _SECTION_RECORDS.items()},
            # "1"/"0" on the wire; the two readers of each used to spell the
            # comparison themselves, in two different ways.
            dashboard_enabled=flag("dashboard", "enabled").strip()
            not in {"", "0", "false", "False"},
            loopback_port=int(flag("loopback", "port")),
            random_favs_browser=RandomFavsBrowserSettings(
                enabled=flag("random_favs_browser", "enabled") == "1",
                shortcut_path=flag("random_favs_browser", "shortcut_path"),
                manifest_file=flag("random_favs_browser", "manifest_file"),
            ),
            layout=LayoutConfig(
                primary_monitor=int(flag("layout", "primary_monitor")),
                secondary_monitor=int(flag("layout", "secondary_monitor")),
                main_top_ratio=float(flag("layout", "main_top_ratio")),
                landscape_width_ratio=float(flag("layout", "landscape_width_ratio")),
            ),
        )
