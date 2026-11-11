from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from app_support import haglio_settings, kino_folder, ports, state_files
from app_support.config_reader import (
    optional_section,
    require_path,
    require_section,
    require_typed,
    require_value,
    resolve_path,
)
from app_support.flick_folder import flicks_folder_in

from .fun_time_funestras import FunTimeFunestra
from .loopback_server import LOOPBACK_PORT
from .project_paths import PROJECT_DIR

DEFAULT_CONFIG_PATH = PROJECT_DIR / "fun_time_config.json"
EXAMPLE_CONFIG_PATH = PROJECT_DIR / "fun_time_config.example.json"

SIBLING_CHECKOUTS_KEY = "sibling_checkouts"
SIBLING_CHECKOUTS_KEY_BEFORE_THE_RENAME = "genau_project_dirs"
RANDOM_FAVES_BROWSER_KEY = "random_faves_browser"
RANDOM_FAVES_BROWSER_KEY_BEFORE_THE_RENAME = "random_favs_browser"


@dataclass(frozen=True)
class PathsConfig:
    ahk_exe: Path
    python_exe: Path
    main_funestra_library_dirs: tuple[Path, ...]
    portrait_dirs: tuple[Path, ...]
    landscape_dirs: tuple[Path, ...]
    trash_dir: Path
    genau_dir: Path
    genau_audio_dir: Path
    state_dir: Path
    broker_state_dir: Path
    sibling_checkouts: tuple[Path, ...] = ()
    broker_tray_launcher: Path | None = None
    origenerator_dir: Path | None = None
    origenerator_python_exe: Path | None = None

    @property
    def flicks_dir(self) -> Path:
        found = flicks_folder_in(self.genau_dir)
        if found is None:
            raise FileNotFoundError(f"No folder in {self.genau_dir} is laid out as Genau's "
                                    f"{haglio_settings.value('genau_flick_words', 'many')} are")
        return found

    @property
    def sibling_checkouts_path(self) -> str:
        return os.pathsep.join(str(path) for path in self.sibling_checkouts)


@dataclass(frozen=True)
class LayoutConfig:
    primary_monitor: int
    secondary_monitor: int
    main_top_ratio: float
    landscape_width_ratio: float


@dataclass(frozen=True)
class AudioCompanionConfig:
    host: str
    port: int


@dataclass(frozen=True)
class RandomFavesBrowserConfig:
    enabled: bool
    shortcut_path: Path
    user_data_dir: Path
    profile_name: str
    open_count: int
    lazy_load: bool


@dataclass(frozen=True)
class RegenConfig:
    generate_video_url: str = "https://example.com/video"
    generate_image_url: str = "https://example.com/create"
    media_root: Path | None = None
    metadata_root: Path | None = None


@dataclass(frozen=True)
class VoiceControlConfig:
    enabled: bool
    model_path: str
    device_name: str | None = None
    sample_rate: int = 16000
    confidence_threshold: float = 0.7
    confirm_commands: bool = True


@dataclass(frozen=True)
class VrConfig:
    # compositor_layers is off by default: the bundled Pimax OpenXR 0.1.0 accepts quad layers in
    # xrEndFrame and then never composites them.

    library_dirs: tuple[Path, ...] = ()
    audio_device: str | None = None
    compositor_layers: bool = False


@dataclass(frozen=True)
class SatelliteChannel:
    cmd_file: Path
    paused_file: Path
    status_file: Path
    playlist_file: Path
    notice_file: Path
    hud_file: Path
    origenerator_hud_file: Path
    sources: str = ""

    @classmethod
    def in_state_dir(cls, state_dir: Path, funestra: FunTimeFunestra, *,
                     sources: str = "") -> SatelliteChannel:
        label = FunTimeFunestra(funestra).label
        return cls(
            cmd_file=state_dir / f"{label}_cmd.txt",
            paused_file=state_dir / f"{label}_paused.txt",
            status_file=state_dir / f"{label}_status.txt",
            playlist_file=state_dir / f"{label}_playlist.tsv",
            notice_file=state_dir / f"{label}_notice.txt",
            hud_file=state_dir / f"{label}_hud.json",
            origenerator_hud_file=state_dir / f"origenerator_{label}_hud.json",
            sources=sources,
        )

    @classmethod
    def from_manifest_section(cls, values: Mapping[str, str]) -> SatelliteChannel:
        return cls(sources=values.get("sources", ""),
                   **{name: Path(values[name]) for name in _FILES})

    def as_manifest_section(self) -> dict[str, str]:
        return {field.name: str(getattr(self, field.name)) for field in fields(self)}


_FILES = tuple(field.name for field in fields(SatelliteChannel) if field.name != "sources")


@dataclass(frozen=True)
class ProjectConfig:
    project_dir: Path
    config_path: Path
    paths: PathsConfig
    layout: LayoutConfig
    audio_companion: AudioCompanionConfig
    random_faves_browser: RandomFavesBrowserConfig
    voice_control: VoiceControlConfig
    regen: RegenConfig
    loopback_port: int = LOOPBACK_PORT
    osr2_slot_port: int = ports.OSR2_SLOT
    vr: VrConfig = VrConfig()
    instance_id_override: str | None = None

    @property
    def instance_id(self) -> str:
        return self.instance_id_override or str(self.config_path)

    @property
    def broker_mode_file(self) -> Path:
        return self.paths.broker_state_dir / state_files.BROKER_MODE

    @property
    def broker_cmd_file(self) -> Path:
        return self.paths.broker_state_dir / state_files.BROKER_CMD

    @property
    def genau_cmd_file(self) -> Path:
        return self.paths.state_dir / state_files.GENAU_CMD

    @property
    def genau_paused_file(self) -> Path:
        return self.paths.state_dir / state_files.GENAU_PAUSED

    @property
    def genau_status_file(self) -> Path:
        return self.paths.state_dir / state_files.GENAU_STATUS

    @property
    def main_funestra_cmd_file(self) -> Path:
        return self.paths.state_dir / "main_player_cmd.txt"

    @property
    def main_funestra_paused_file(self) -> Path:
        return self.paths.state_dir / "main_player_paused.txt"

    @property
    def main_funestra_status_file(self) -> Path:
        return self.paths.state_dir / "main_player_status.txt"

    @property
    def main_funestra_notice_file(self) -> Path:
        return self.paths.state_dir / "main_player_notice.txt"

    @property
    def main_funestra_playlist_file(self) -> Path:
        return self.paths.state_dir / "main_player_playlist.tsv"

    @property
    def origenerator_cmd_file(self) -> Path:
        return self.paths.state_dir / "origenerator_cmd.txt"

    @property
    def origenerator_paused_file(self) -> Path:
        return self.paths.state_dir / "origenerator_paused.txt"

    @property
    def origenerator_status_file(self) -> Path:
        return self.paths.state_dir / "origenerator_status.txt"

    @property
    def origenerator_frames_file(self) -> Path:
        return self.paths.state_dir / "origenerator_frame.bin"

    @property
    def origenerator_input_file(self) -> Path:
        return self.paths.state_dir / "origenerator_input.txt"

    @property
    def audio_paused_file(self) -> Path:
        return self.paths.state_dir / "audio_paused.txt"

    @property
    def audio_volume_file(self) -> Path:
        return self.paths.state_dir / "audio_volume.txt"

    @property
    def random_faves_browser_manifest_file(self) -> Path:
        return self.paths.state_dir / "random_faves_browser_urls.txt"

    def satellite(self, funestra: FunTimeFunestra) -> SatelliteChannel:
        dirs = (self.paths.portrait_dirs if FunTimeFunestra(funestra) is FunTimeFunestra.PORTRAIT
                else self.paths.landscape_dirs)
        return SatelliteChannel.in_state_dir(
            self.paths.state_dir, funestra, sources="|".join(str(path) for path in dirs))

    @property
    def dashboard_state_file(self) -> Path:
        return self.paths.state_dir / "dashboard_state.ini"

    @property
    def dashboard_cmd_file(self) -> Path:
        return self.paths.state_dir / "dashboard_cmd.txt"

    @property
    def logs_dir(self) -> Path:
        return self.paths.state_dir

    def log_file(self, name: str) -> Path:
        return self.logs_dir / f"{name}.log"


def _resolve_config_path(config_path: str | Path | None, project_dir: Path) -> Path:
    path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = (project_dir / path).resolve()
    return path


def _load_paths_config(paths_raw: dict[str, Any], source_path: Path, project_dir: Path) -> PathsConfig:
    main_funestra_library_dirs_raw = require_value(paths_raw, "main_player_library_dirs", source_path, context="config.paths")
    if not isinstance(main_funestra_library_dirs_raw, list):
        raise TypeError("paths.main_player_library_dirs must be a list of folder paths")
    if not main_funestra_library_dirs_raw:
        raise ValueError("paths.main_player_library_dirs must include at least one folder path")

    state_dir = require_path(paths_raw, "state_dir", source_path, base=project_dir, context="config.paths")
    return PathsConfig(
        ahk_exe=require_path(paths_raw, "ahk_exe", source_path, base=project_dir, context="config.paths"),
        python_exe=require_path(paths_raw, "python_exe", source_path, base=project_dir, context="config.paths"),
        main_funestra_library_dirs=tuple(resolve_path(project_dir, str(value)) for value in main_funestra_library_dirs_raw),
        portrait_dirs=(kino_folder.upscales_in(kino_folder.kino_dir()) / "portrait",),
        landscape_dirs=(kino_folder.upscales_in(kino_folder.kino_dir()) / "landscape",),
        trash_dir=kino_folder.trash_folder_in(kino_folder.kino_dir()),
        genau_dir=Path(haglio_settings.value("genau_dir")),
        genau_audio_dir=Path(haglio_settings.value("genau_audio_dir")),
        state_dir=state_dir,
        broker_state_dir=resolve_path(project_dir, paths_raw["broker_state_dir"]) if paths_raw.get("broker_state_dir") else state_dir,
        sibling_checkouts=tuple(
            resolve_path(project_dir, str(value))
            for value in paths_raw.get(SIBLING_CHECKOUTS_KEY, paths_raw.get(
                SIBLING_CHECKOUTS_KEY_BEFORE_THE_RENAME, []))),
        broker_tray_launcher=resolve_path(project_dir, paths_raw["broker_tray_launcher"]) if paths_raw.get("broker_tray_launcher") else None,
        origenerator_dir=resolve_path(project_dir, paths_raw["origenerator_dir"]) if paths_raw.get("origenerator_dir") else None,
        origenerator_python_exe=resolve_path(project_dir, paths_raw["origenerator_python_exe"]) if paths_raw.get("origenerator_python_exe") else None,
    )


def _load_layout_config(layout_raw: dict[str, Any], source_path: Path) -> LayoutConfig:
    primary_monitor = layout_raw.get("primary_monitor")
    secondary_monitor = layout_raw.get("secondary_monitor")
    if primary_monitor is None:
        raise ValueError(f"Missing required config value: config.layout.primary_monitor (in {source_path})")
    if secondary_monitor is None:
        raise ValueError(f"Missing required config value: config.layout.secondary_monitor (in {source_path})")
    return LayoutConfig(
        primary_monitor=int(primary_monitor),
        secondary_monitor=int(secondary_monitor),
        main_top_ratio=require_typed(layout_raw, "main_top_ratio", source_path, cast=float, context="config.layout"),
        landscape_width_ratio=require_typed(layout_raw, "landscape_width_ratio", source_path, cast=float, context="config.layout"),
    )


def _load_audio_companion_config(audio_raw: dict[str, Any], source_path: Path) -> AudioCompanionConfig:
    return AudioCompanionConfig(
        host=require_typed(audio_raw, "host", source_path, cast=str, context="config.audio_companion"),
        port=require_typed(audio_raw, "port", source_path, cast=int, context="config.audio_companion"),
    )


def _load_random_faves_browser_config(
    browser_values: dict[str, Any], project_dir: Path
) -> RandomFavesBrowserConfig:
    default_user_data_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    shortcut = browser_values.get("shortcut_path")
    profile = browser_values.get("profile_name")
    return RandomFavesBrowserConfig(
        enabled=bool(browser_values.get("enabled", False)) and bool(profile),
        shortcut_path=resolve_path(project_dir, str(shortcut or "random_faves_browser.lnk")),
        user_data_dir=resolve_path(project_dir, str(browser_values.get("user_data_dir", default_user_data_dir))),
        profile_name=str(profile or ""),
        open_count=int(browser_values.get("open_count", 10)),
        lazy_load=bool(browser_values.get("lazy_load", False)),
    )


def _load_regen_config(raw: dict[str, Any] | None, project_dir: Path) -> RegenConfig:
    values = raw or {}
    media_root = values.get("media_root")
    metadata_root = values.get("metadata_root")
    return RegenConfig(
        generate_video_url=str(values.get("generate_video_url", "https://example.com/video")),
        generate_image_url=str(values.get("generate_image_url", "https://example.com/create")),
        media_root=resolve_path(project_dir, str(media_root)) if media_root else None,
        metadata_root=resolve_path(project_dir, str(metadata_root)) if metadata_root else None,
    )


def _load_voice_control_config(voice_raw: dict[str, Any] | None) -> VoiceControlConfig:
    values = voice_raw or {}
    return VoiceControlConfig(
        enabled=bool(values.get("enabled", False)),
        model_path=str(values.get("model_path", "vosk-model-en-us-0.22-lgraph")),
        device_name=str(values["device_name"]) if values.get("device_name") is not None else None,
        sample_rate=int(values.get("sample_rate", 16000)),
        confidence_threshold=float(values.get("confidence_threshold", 0.7)),
        confirm_commands=bool(values.get("confirm_commands", True)),
    )


def _load_vr_config(raw: dict[str, Any] | None, project_dir: Path) -> VrConfig:
    values = raw or {}
    library_dirs_raw = values.get("library_dirs", [])
    if not isinstance(library_dirs_raw, list):
        raise TypeError("vr.library_dirs must be a list of folder paths")
    audio_device = values.get("audio_device")
    return VrConfig(
        library_dirs=tuple(resolve_path(project_dir, str(value)) for value in library_dirs_raw),
        audio_device=str(audio_device) if audio_device else None,
        compositor_layers=bool(values.get("compositor_layers", False)),
    )


def _write_a_starter_whose_paths_are_placeholders(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE_CONFIG_PATH, path)


def _raise_for_missing_config(path: Path) -> None:
    if path == DEFAULT_CONFIG_PATH and EXAMPLE_CONFIG_PATH.exists():
        _write_a_starter_whose_paths_are_placeholders(path)
        raise FileNotFoundError(
            f"No config found, so a starter copy was written from "
            f"{EXAMPLE_CONFIG_PATH.name} to {path}. Fill in your real paths "
            f"(library folders, python_exe, ...) and run again."
        )
    raise FileNotFoundError(
        f"Config file not found: {path}. Copy {EXAMPLE_CONFIG_PATH.name} to it "
        f"and fill in your real paths."
    )


def load_config(config_path: str | Path | None = None, *, project_dir: Path | None = None) -> ProjectConfig:
    project_dir = project_dir or PROJECT_DIR
    path = _resolve_config_path(config_path, project_dir)
    if not path.exists():
        _raise_for_missing_config(path)
    with path.open("r", encoding="utf-8") as fp:
        raw: dict[str, Any] = json.load(fp)

    paths_raw = require_section(raw, "paths", path)
    layout_raw = require_section(raw, "layout", path)
    audio_raw = require_section(raw, "audio_companion", path)
    browser_raw = {
        **(optional_section(raw, RANDOM_FAVES_BROWSER_KEY_BEFORE_THE_RENAME, path) or {}),
        **(optional_section(raw, RANDOM_FAVES_BROWSER_KEY, path) or {}),
    }
    voice_raw = optional_section(raw, "voice_control", path)
    regen_raw = optional_section(raw, "regen", path)
    vr_raw = optional_section(raw, "vr", path)

    return ProjectConfig(
        project_dir=project_dir,
        config_path=path,
        paths=_load_paths_config(paths_raw, path, project_dir),
        layout=_load_layout_config(layout_raw, path),
        audio_companion=_load_audio_companion_config(audio_raw, path),
        random_faves_browser=_load_random_faves_browser_config(browser_raw, project_dir),
        voice_control=_load_voice_control_config(voice_raw),
        regen=_load_regen_config(regen_raw, project_dir),
        loopback_port=int(raw.get("loopback_port", LOOPBACK_PORT)),
        osr2_slot_port=int(raw.get("osr2_slot_port", ports.OSR2_SLOT)),
        vr=_load_vr_config(vr_raw, project_dir),
        instance_id_override=str(raw["instance_id"]) if raw.get("instance_id") else None,
    )
