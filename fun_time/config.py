from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "fun_time_config.json"


def _resolve_path(project_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (project_dir / path).resolve()


def _require_dict(parent: dict[str, Any], key: str, source_path: Path, context: str = "config") -> dict[str, Any]:
    value = parent.get(key)
    dotted = f"{context}.{key}" if context else key
    if value is None:
        raise ValueError(f"Missing required config section: {dotted} (in {source_path})")
    if not isinstance(value, dict):
        raise TypeError(f"Expected object for config section: {dotted} (in {source_path})")
    return value


def _require_value(parent: dict[str, Any], key: str, source_path: Path, context: str) -> Any:
    value = parent.get(key)
    dotted = f"{context}.{key}"
    if value is None:
        raise ValueError(f"Missing required config value: {dotted} (in {source_path})")
    return value


@dataclass(frozen=True)
class PathsConfig:
    vlc_exe: Path
    ahk_exe: Path
    python_exe: Path
    primary_vlc_dirs: tuple[Path, ...]
    portrait_dirs: tuple[Path, ...]
    landscape_dirs: tuple[Path, ...]
    weird_dir: Path
    clips_dir: Path
    audio_dir: Path
    favs_file: Path
    state_dir: Path
    genau_python_exe: Path | None = None
    genau_config_path: Path | None = None
    broker_tray_launcher: Path | None = None

    @property
    def primary_vlc_dir(self) -> Path:
        return self.primary_vlc_dirs[0]

    @property
    def portrait_dir(self) -> Path:
        return self.portrait_dirs[0]

    @property
    def landscape_dir(self) -> Path:
        return self.landscape_dirs[0]


@dataclass(frozen=True)
class LayoutConfig:
    main_monitor: int
    secondary_monitor: int
    primary_top_ratio: float
    landscape_width_ratio: float


@dataclass(frozen=True)
class VlcConfig:
    vlc2_http_port: int
    vlc3_http_port: int


@dataclass(frozen=True)
class GenauConfig:
    shuffle_on_load: bool
    beats_per_loop: float
    clip_cache_size: int
    render_batch: int
    bpm_smoothing: float
    sync_strength: float
    udp_host: str
    udp_port: int
    notify_host: str
    notify_port: int
    status_hide_ms: int
    resize_debounce_ms: int


@dataclass(frozen=True)
class AudioCompanionConfig:
    host: str
    port: int


@dataclass(frozen=True)
class RandomFavsBrowserConfig:
    enabled: bool
    shortcut_path: Path
    user_data_dir: Path
    profile_name: str
    open_count: int
    lazy_load: bool


@dataclass(frozen=True)
class ProviderRegenConfig:
    generate_video_url: str = "https://example.com/video"
    generate_image_url: str = "https://example.com/create"
    media_root: Path | None = None
    metadata_root: Path | None = None


@dataclass(frozen=True)
class VoiceControlConfig:
    enabled: bool
    model_path: str
    device_index: int | None = None
    sample_rate: int = 16000
    confidence_threshold: float = 0.7


@dataclass(frozen=True)
class ProjectConfig:
    project_dir: Path
    config_path: Path
    paths: PathsConfig
    vlc: VlcConfig
    layout: LayoutConfig
    genau: GenauConfig
    audio_companion: AudioCompanionConfig
    random_favs_browser: RandomFavsBrowserConfig
    voice_control: VoiceControlConfig
    provider_regen: ProviderRegenConfig

    @property
    def genau_mode_file(self) -> Path:
        return self.paths.state_dir / "genau_mode.txt"

    @property
    def genau_cmd_file(self) -> Path:
        return self.paths.state_dir / "genau_cmd.txt"

    @property
    def genau_paused_file(self) -> Path:
        return self.paths.state_dir / "genau_paused.txt"

    @property
    def nau_cmd_file(self) -> Path:
        return self.paths.state_dir / "nau_cmd.txt"

    @property
    def nau_paused_file(self) -> Path:
        return self.paths.state_dir / "nau_paused.txt"

    @property
    def nau_status_file(self) -> Path:
        return self.paths.state_dir / "nau_status.txt"

    @property
    def nau_playlist_file(self) -> Path:
        return self.paths.state_dir / "nau_playlist.tsv"

    @property
    def audio_cmd_file(self) -> Path:
        return self.paths.state_dir / "audio_cmd.txt"

    @property
    def audio_paused_file(self) -> Path:
        return self.paths.state_dir / "audio_paused.txt"

    @property
    def random_favs_browser_manifest_file(self) -> Path:
        return self.paths.state_dir / "random_favs_browser_urls.txt"

    @property
    def logs_dir(self) -> Path:
        return self.paths.state_dir

    def log_file(self, name: str) -> Path:
        return self.logs_dir / f"{name}.log"


def _resolve_config_path(config_path: str | Path | None) -> Path:
    path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = (PROJECT_DIR / path).resolve()
    return path


def _require_optional_dict(parent: dict[str, Any], key: str, source_path: Path) -> dict[str, Any] | None:
    value = parent.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"Expected object for config section: {key} (in {source_path})")
    return value


def _require_path_value(parent: dict[str, Any], key: str, source_path: Path, context: str) -> Path:
    return _resolve_path(PROJECT_DIR, _require_value(parent, key, source_path, context))


def _require_typed_value(
    parent: dict[str, Any],
    key: str,
    source_path: Path,
    context: str,
    cast: type,
):
    return cast(_require_value(parent, key, source_path, context))


def _load_paths_config(paths_raw: dict[str, Any], source_path: Path) -> PathsConfig:
    primary_vlc_dirs_raw = _require_value(paths_raw, "primary_vlc_dirs", source_path, "config.paths")
    if not isinstance(primary_vlc_dirs_raw, list):
        raise TypeError("paths.primary_vlc_dirs must be a list of folder paths")
    if not primary_vlc_dirs_raw:
        raise ValueError("paths.primary_vlc_dirs must include at least one folder path")

    return PathsConfig(
        vlc_exe=_require_path_value(paths_raw, "vlc_exe", source_path, "config.paths"),
        ahk_exe=_require_path_value(paths_raw, "ahk_exe", source_path, "config.paths"),
        python_exe=_require_path_value(paths_raw, "python_exe", source_path, "config.paths"),
        primary_vlc_dirs=tuple(_resolve_path(PROJECT_DIR, str(value)) for value in primary_vlc_dirs_raw),
        portrait_dirs=_load_dir_list(paths_raw, "portrait_dirs", "portrait_dir", source_path),
        landscape_dirs=_load_dir_list(paths_raw, "landscape_dirs", "landscape_dir", source_path),
        weird_dir=_require_path_value(paths_raw, "weird_dir", source_path, "config.paths"),
        clips_dir=_require_path_value(paths_raw, "clips_dir", source_path, "config.paths"),
        audio_dir=_require_path_value(paths_raw, "audio_dir", source_path, "config.paths"),
        favs_file=_require_path_value(paths_raw, "favs_file", source_path, "config.paths"),
        state_dir=_require_path_value(paths_raw, "state_dir", source_path, "config.paths"),
        genau_python_exe=_resolve_path(PROJECT_DIR, paths_raw["genau_python_exe"]) if paths_raw.get("genau_python_exe") else None,
        genau_config_path=_resolve_path(PROJECT_DIR, paths_raw["genau_config_path"]) if paths_raw.get("genau_config_path") else None,
        broker_tray_launcher=_resolve_path(PROJECT_DIR, paths_raw["broker_tray_launcher"]) if paths_raw.get("broker_tray_launcher") else None,
    )


def _load_layout_config(layout_raw: dict[str, Any], source_path: Path) -> LayoutConfig:
    main_monitor = layout_raw.get("main_monitor")
    secondary_monitor = layout_raw.get("secondary_monitor")
    if main_monitor is None:
        raise ValueError(f"Missing required config value: config.layout.main_monitor (in {source_path})")
    if secondary_monitor is None:
        raise ValueError(f"Missing required config value: config.layout.secondary_monitor (in {source_path})")
    return LayoutConfig(
        main_monitor=int(main_monitor),
        secondary_monitor=int(secondary_monitor),
        primary_top_ratio=_require_typed_value(layout_raw, "primary_top_ratio", source_path, "config.layout", float),
        landscape_width_ratio=_require_typed_value(layout_raw, "landscape_width_ratio", source_path, "config.layout", float),
    )


def _load_vlc_config(vlc_raw: dict[str, Any], source_path: Path) -> VlcConfig:
    return VlcConfig(
        vlc2_http_port=_require_typed_value(vlc_raw, "vlc2_http_port", source_path, "config.vlc", int),
        vlc3_http_port=_require_typed_value(vlc_raw, "vlc3_http_port", source_path, "config.vlc", int),
    )


def _load_genau_config(genau_raw: dict[str, Any], source_path: Path) -> GenauConfig:
    return GenauConfig(
        shuffle_on_load=_require_typed_value(genau_raw, "shuffle_on_load", source_path, "config.genau", bool),
        beats_per_loop=_require_typed_value(genau_raw, "beats_per_loop", source_path, "config.genau", float),
        clip_cache_size=_require_typed_value(genau_raw, "clip_cache_size", source_path, "config.genau", int),
        render_batch=_require_typed_value(genau_raw, "render_batch", source_path, "config.genau", int),
        bpm_smoothing=_require_typed_value(genau_raw, "bpm_smoothing", source_path, "config.genau", float),
        sync_strength=_require_typed_value(genau_raw, "sync_strength", source_path, "config.genau", float),
        udp_host=_require_typed_value(genau_raw, "udp_host", source_path, "config.genau", str),
        udp_port=_require_typed_value(genau_raw, "udp_port", source_path, "config.genau", int),
        notify_host=_require_typed_value(genau_raw, "notify_host", source_path, "config.genau", str),
        notify_port=_require_typed_value(genau_raw, "notify_port", source_path, "config.genau", int),
        status_hide_ms=_require_typed_value(genau_raw, "status_hide_ms", source_path, "config.genau", int),
        resize_debounce_ms=_require_typed_value(genau_raw, "resize_debounce_ms", source_path, "config.genau", int),
    )


def _load_audio_companion_config(audio_raw: dict[str, Any], source_path: Path) -> AudioCompanionConfig:
    return AudioCompanionConfig(
        host=_require_typed_value(audio_raw, "host", source_path, "config.audio_companion", str),
        port=_require_typed_value(audio_raw, "port", source_path, "config.audio_companion", int),
    )


def _load_random_favs_browser_config(browser_raw: dict[str, Any] | None) -> RandomFavsBrowserConfig:
    default_user_data_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    browser_values = browser_raw or {}
    return RandomFavsBrowserConfig(
        enabled=bool(browser_values.get("enabled", False)),
        shortcut_path=_resolve_path(PROJECT_DIR, str(browser_values.get("shortcut_path", "Blair Chrome.lnk"))),
        user_data_dir=_resolve_path(PROJECT_DIR, str(browser_values.get("user_data_dir", default_user_data_dir))),
        profile_name=str(browser_values.get("profile_name", "Blair")),
        open_count=int(browser_values.get("open_count", 10)),
        lazy_load=bool(browser_values.get("lazy_load", False)),
    )


def _load_provider_regen_config(raw: dict[str, Any] | None) -> ProviderRegenConfig:
    values = raw or {}
    media_root = values.get("media_root")
    metadata_root = values.get("metadata_root")
    return ProviderRegenConfig(
        generate_video_url=str(values.get("generate_video_url", "https://example.com/video")),
        generate_image_url=str(values.get("generate_image_url", "https://example.com/create")),
        media_root=_resolve_path(PROJECT_DIR, str(media_root)) if media_root else None,
        metadata_root=_resolve_path(PROJECT_DIR, str(metadata_root)) if metadata_root else None,
    )


def _load_voice_control_config(voice_raw: dict[str, Any] | None) -> VoiceControlConfig:
    values = voice_raw or {}
    return VoiceControlConfig(
        enabled=bool(values.get("enabled", False)),
        model_path=str(values.get("model_path", "vosk-model-small-en-us-0.15")),
        device_index=int(values["device_index"]) if values.get("device_index") is not None else None,
        sample_rate=int(values.get("sample_rate", 16000)),
        confidence_threshold=float(values.get("confidence_threshold", 0.7)),
    )


def load_config(config_path: str | Path | None = None) -> ProjectConfig:
    path = _resolve_config_path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        raw: dict[str, Any] = json.load(fp)

    paths_raw = _require_dict(raw, "paths", path)
    vlc_raw = _require_dict(raw, "vlc", path)
    layout_raw = _require_dict(raw, "layout", path)
    genau_raw = _require_dict(raw, "genau", path)
    audio_raw = _require_dict(raw, "audio_companion", path)
    browser_raw = _require_optional_dict(raw, "random_favs_browser", path)
    if browser_raw is None:
        browser_raw = _require_optional_dict(raw, "chrome_overlay", path)
    voice_raw = _require_optional_dict(raw, "voice_control", path)
    provider_regen_raw = _require_optional_dict(raw, "provider_regen", path)

    return ProjectConfig(
        project_dir=PROJECT_DIR,
        config_path=path,
        paths=_load_paths_config(paths_raw, path),
        vlc=_load_vlc_config(vlc_raw, path),
        layout=_load_layout_config(layout_raw, path),
        genau=_load_genau_config(genau_raw, path),
        audio_companion=_load_audio_companion_config(audio_raw, path),
        random_favs_browser=_load_random_favs_browser_config(browser_raw),
        voice_control=_load_voice_control_config(voice_raw),
        provider_regen=_load_provider_regen_config(provider_regen_raw),
    )


def _load_dir_list(paths_raw: dict[str, Any], list_key: str, single_key: str, source_path: Path) -> tuple[Path, ...]:
    values = paths_raw.get(list_key)
    if values is None:
        return (_resolve_path(PROJECT_DIR, str(_require_value(paths_raw, single_key, source_path, "config.paths"))),)
    if not isinstance(values, list):
        raise TypeError(f"paths.{list_key} must be a list of folder paths")
    if not values:
        raise ValueError(f"paths.{list_key} must include at least one folder path")
    return tuple(_resolve_path(PROJECT_DIR, str(value)) for value in values)
