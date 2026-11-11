from __future__ import annotations

import configparser
from dataclasses import MISSING, dataclass, fields
from pathlib import Path

from app_support import ports, state_files

from .config import LayoutConfig, RegenConfig, SatelliteChannel
from .fun_time_funestras import FunTimeFunestra
from .main_funestra_hud import main_funestra_hud_path

WINDOWS_BRIDGE_MANIFEST_FILENAME = "windows_bridge_launch.ini"
_SWITCHED_ON, _SWITCHED_OFF = "1", "0"


def _as_written(switched_on: bool) -> str:
    return _SWITCHED_ON if switched_on else _SWITCHED_OFF


def _is_on_unless_blank_or_spelled_off(flag: str) -> bool:
    return flag.strip() not in {"", _SWITCHED_OFF, "false", "False"}


def _is_on_only_as_the_writer_spells_it(flag: str) -> bool:
    return flag == _SWITCHED_ON


def _parser_keeping_every_keys_spelling() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    return parser


def build_windows_bridge_manifest(
        config, *, dashboard_enabled: bool = True) -> dict[str, dict[str, str]]:
    layout = config.layout
    return {
        "runtime": {
            "config_path": str(config.config_path),
            "windows_bridge_log_file": str(config.log_file("windows_bridge")),
            "sibling_checkouts": config.paths.sibling_checkouts_path,
            "origenerator_dir": str(config.paths.origenerator_dir or ""),
        },
        "executables": {
            "python_exe": str(config.paths.python_exe),
            "origenerator_python_exe": str(config.paths.origenerator_python_exe or ""),
        },
        "media": {
            "main_funestra_library_sources": "|".join(str(path) for path in config.paths.main_funestra_library_dirs),
            "vr_library_dirs": "",
            "trash_dir": str(config.paths.trash_dir),
            "genau_flicks": str(config.paths.flicks_dir),
            "genau_vr_flicks": "",
            "genau_audio": str(config.paths.genau_audio_dir),
        },
        "modules": {
            "main_funestra_module": "main_funestra",
            "satellite_module": "satellite",
            "audio_module": "fun_time.audio_companion_app",
            "dashboard_module": "fun_time.dashboard_app",
        },
        "commands": {
            "broker_mode_file": str(config.broker_mode_file),
            "genau_cmd_file": str(config.genau_cmd_file),
            "genau_paused_file": str(config.genau_paused_file),
            "genau_status_file": str(config.genau_status_file),
            "main_funestra_cmd_file": str(config.main_funestra_cmd_file),
            "main_funestra_paused_file": str(config.main_funestra_paused_file),
            "main_funestra_status_file": str(config.main_funestra_status_file),
            "main_funestra_console_file": str(main_funestra_hud_path(config.paths.state_dir)),
            "main_funestra_playlist_file": str(config.main_funestra_playlist_file),
            "broker_cmd_file": str(config.broker_cmd_file),
            "broker_state_dir": str(config.paths.broker_state_dir),
            "broker_tray_launcher": str(config.paths.broker_tray_launcher or ""),
            "audio_paused_file": str(config.audio_paused_file),
            "audio_volume_file": str(config.audio_volume_file),
            "dashboard_state_file": str(config.dashboard_state_file),
            "dashboard_cmd_file": str(config.dashboard_cmd_file),
            "state_dir": str(config.paths.state_dir),
            "main_funestra_notice_file": str(config.main_funestra_notice_file),
            "origenerator_cmd_file": str(config.origenerator_cmd_file),
            "origenerator_paused_file": str(config.origenerator_paused_file),
            "origenerator_status_file": str(config.origenerator_status_file),
            "origenerator_frames_file": str(config.origenerator_frames_file),
            "origenerator_input_file": str(config.origenerator_input_file),
            "osr2_slot_port": str(config.osr2_slot_port),
        },
        **{funestra.label: config.satellite(funestra).as_manifest_section()
           for funestra in FunTimeFunestra.SATELLITES},
        "dashboard": {
            "enabled": _as_written(dashboard_enabled),
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
        "random_faves_browser": {
            "enabled": _as_written(config.random_faves_browser.enabled),
            "shortcut_path": str(config.random_faves_browser.shortcut_path),
            "manifest_file": str(config.random_faves_browser_manifest_file),
        },
        "regen": {
            "generate_video_url": config.regen.generate_video_url,
            "generate_image_url": config.regen.generate_image_url,
            "media_root": str(config.regen.media_root or ""),
            "metadata_root": str(config.regen.metadata_root or ""),
        },
    }


def write_manifest_data(data: dict[str, dict[str, str]], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    parser = _parser_keeping_every_keys_spelling()
    parser.read_dict(data)
    with destination.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return destination


def write_windows_bridge_manifest(
        config, destination: Path | None = None, *, dashboard_enabled: bool = True) -> Path:
    manifest_path = destination or (config.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME)
    return write_manifest_data(
        build_windows_bridge_manifest(config, dashboard_enabled=dashboard_enabled), manifest_path)


class ManifestKeyMissing(LookupError):
    pass


@dataclass(frozen=True)
class RuntimePaths:
    config_path: str
    windows_bridge_log_file: str
    sibling_checkouts: str = ""
    origenerator_dir: str = ""


@dataclass(frozen=True)
class Executables:
    python_exe: str
    origenerator_python_exe: str = ""


@dataclass(frozen=True)
class MediaSources:
    main_funestra_library_sources: str
    trash_dir: str
    genau_flicks: str
    genau_audio: str
    vr_library_dirs: str = ""
    genau_vr_flicks: str = ""


@dataclass(frozen=True)
class ChildModules:
    main_funestra_module: str
    satellite_module: str
    audio_module: str
    dashboard_module: str


@dataclass(frozen=True)
class CommandFiles:
    broker_mode_file: str
    genau_cmd_file: str
    genau_paused_file: str
    genau_status_file: str
    main_funestra_cmd_file: str
    main_funestra_paused_file: str
    main_funestra_status_file: str
    main_funestra_console_file: str
    main_funestra_playlist_file: str
    broker_cmd_file: str
    audio_paused_file: str
    audio_volume_file: str
    dashboard_state_file: str
    dashboard_cmd_file: str
    state_dir: str
    main_funestra_notice_file: str
    origenerator_status_file: str
    broker_state_dir: str = ""
    broker_tray_launcher: str = ""
    origenerator_cmd_file: str = ""
    origenerator_paused_file: str = ""
    origenerator_frames_file: str = ""
    origenerator_input_file: str = ""
    osr2_slot_port: str = str(ports.OSR2_SLOT)

    @property
    def genau_drive_file(self) -> str:
        return str(Path(self.genau_cmd_file).parent / state_files.GENAU_DRIVE)


@dataclass(frozen=True)
class RandomFavesBrowserSettings:
    enabled: bool
    shortcut_path: str
    manifest_file: str


@dataclass(frozen=True)
class RegenSettings:
    generate_video_url: str = RegenConfig.generate_video_url
    generate_image_url: str = RegenConfig.generate_image_url
    media_root: str = ""
    metadata_root: str = ""

    @property
    def metadata_folder(self) -> Path | None:
        named = self.metadata_root.strip()
        return Path(named) if named else None


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
    runtime: RuntimePaths
    executables: Executables
    media: MediaSources
    modules: ChildModules
    commands: CommandFiles
    dashboard_enabled: bool
    loopback_port: int
    layout: LayoutConfig
    random_faves_browser: RandomFavesBrowserSettings
    regen: RegenSettings
    portrait: SatelliteChannel
    landscape: SatelliteChannel

    def satellite(self, funestra: FunTimeFunestra) -> SatelliteChannel:
        return (self.portrait if FunTimeFunestra(funestra) is FunTimeFunestra.PORTRAIT
                else self.landscape)

    @classmethod
    def read(cls, path: str | Path) -> LaunchManifest:
        path = Path(path)
        parser = _parser_keeping_every_keys_spelling()
        parser.read(str(path), encoding="utf-8")

        def values_of(section: str, kind: type) -> dict[str, str]:
            values = parser[section] if parser.has_section(section) else {}
            missing = [f.name for f in fields(kind)
                       if f.name not in values and f.default is MISSING]
            if missing:
                raise ManifestKeyMissing(
                    f"[{section}] {', '.join(missing)} missing from {path}")
            return {f.name: values[f.name] for f in fields(kind) if f.name in values}

        def record(section: str, kind: type):
            return kind(**values_of(section, kind))

        def flag(section: str, key: str) -> str:
            if not parser.has_option(section, key):
                raise ManifestKeyMissing(f"[{section}] {key} missing from {path}")
            return parser[section][key]

        return cls(
            **{attr: record(section, kind)
               for section, (attr, kind) in _SECTION_RECORDS.items()},
            **{funestra.label: SatelliteChannel.from_manifest_section(
                   values_of(funestra.label, SatelliteChannel))
               for funestra in FunTimeFunestra.SATELLITES},
            dashboard_enabled=_is_on_unless_blank_or_spelled_off(flag("dashboard", "enabled")),
            loopback_port=int(flag("loopback", "port")),
            random_faves_browser=RandomFavesBrowserSettings(
                enabled=_is_on_only_as_the_writer_spells_it(
                    flag("random_faves_browser", "enabled")),
                shortcut_path=flag("random_faves_browser", "shortcut_path"),
                manifest_file=flag("random_faves_browser", "manifest_file"),
            ),
            layout=LayoutConfig(
                primary_monitor=int(flag("layout", "primary_monitor")),
                secondary_monitor=int(flag("layout", "secondary_monitor")),
                main_top_ratio=float(flag("layout", "main_top_ratio")),
                landscape_width_ratio=float(flag("layout", "landscape_width_ratio")),
            ),
        )
