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
    mfp_exe: Path
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
    mfp_width_ratio: float
    mfp_height_ratio: float


@dataclass(frozen=True)
class ControllerConfig:
    primary_vlc_http_port: int
    vlc2_http_port: int
    vlc3_http_port: int
    layout: LayoutConfig


@dataclass(frozen=True)
class BrokerConfig:
    virtual_port: str
    real_port: str
    baud: int
    udp_host: str
    udp_port: int
    auto_stale_timeout: float


@dataclass(frozen=True)
class RobotHandConfig:
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
    bookmarks_folder_name: str
    open_count: int


@dataclass(frozen=True)
class ProjectConfig:
    project_dir: Path
    config_path: Path
    paths: PathsConfig
    controller: ControllerConfig
    broker: BrokerConfig
    robot_hand: RobotHandConfig
    audio_companion: AudioCompanionConfig
    random_favs_browser: RandomFavsBrowserConfig

    @property
    def robot_hand_mode_file(self) -> Path:
        return self.paths.state_dir / "robot_hand_mode.txt"

    @property
    def robot_hand_cmd_file(self) -> Path:
        return self.paths.state_dir / "robot_hand_cmd.txt"

    @property
    def robot_hand_enabled_file(self) -> Path:
        return self.paths.state_dir / "robot_hand_enabled.txt"

    @property
    def robot_hand_paused_file(self) -> Path:
        return self.paths.state_dir / "robot_hand_paused.txt"

    @property
    def broker_cmd_file(self) -> Path:
        return self.paths.state_dir / "broker_cmd.txt"

    @property
    def broker_heartbeat_file(self) -> Path:
        return self.paths.state_dir / "broker_heartbeat.txt"

    @property
    def broker_activity_file(self) -> Path:
        return self.paths.state_dir / "broker_activity.txt"

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
        mfp_exe=_require_path_value(paths_raw, "mfp_exe", source_path, "config.paths"),
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
    )


def _load_layout_config(layout_raw: dict[str, Any], source_path: Path) -> LayoutConfig:
    main_monitor = layout_raw.get("main_monitor")
    secondary_monitor = layout_raw.get("secondary_monitor")
    if main_monitor is None:
        raise ValueError(f"Missing required config value: config.controller.layout.main_monitor (in {source_path})")
    if secondary_monitor is None:
        raise ValueError(f"Missing required config value: config.controller.layout.secondary_monitor (in {source_path})")
    return LayoutConfig(
        main_monitor=int(main_monitor),
        secondary_monitor=int(secondary_monitor),
        primary_top_ratio=_require_typed_value(layout_raw, "primary_top_ratio", source_path, "config.controller.layout", float),
        landscape_width_ratio=_require_typed_value(layout_raw, "landscape_width_ratio", source_path, "config.controller.layout", float),
        mfp_width_ratio=_require_typed_value(layout_raw, "mfp_width_ratio", source_path, "config.controller.layout", float),
        mfp_height_ratio=_require_typed_value(layout_raw, "mfp_height_ratio", source_path, "config.controller.layout", float),
    )


def _load_controller_config(controller_raw: dict[str, Any], source_path: Path) -> ControllerConfig:
    layout_raw = _require_dict(controller_raw, "layout", source_path, "config.controller")
    return ControllerConfig(
        primary_vlc_http_port=_require_typed_value(controller_raw, "primary_vlc_http_port", source_path, "config.controller", int),
        vlc2_http_port=_require_typed_value(controller_raw, "vlc2_http_port", source_path, "config.controller", int),
        vlc3_http_port=_require_typed_value(controller_raw, "vlc3_http_port", source_path, "config.controller", int),
        layout=_load_layout_config(layout_raw, source_path),
    )


def _load_broker_config(broker_raw: dict[str, Any], source_path: Path) -> BrokerConfig:
    return BrokerConfig(
        virtual_port=_require_typed_value(broker_raw, "virtual_port", source_path, "config.broker", str),
        real_port=_require_typed_value(broker_raw, "real_port", source_path, "config.broker", str),
        baud=_require_typed_value(broker_raw, "baud", source_path, "config.broker", int),
        udp_host=_require_typed_value(broker_raw, "udp_host", source_path, "config.broker", str),
        udp_port=_require_typed_value(broker_raw, "udp_port", source_path, "config.broker", int),
        auto_stale_timeout=_require_typed_value(broker_raw, "auto_stale_timeout", source_path, "config.broker", float),
    )


def _load_robot_hand_config(robot_raw: dict[str, Any], source_path: Path) -> RobotHandConfig:
    return RobotHandConfig(
        shuffle_on_load=_require_typed_value(robot_raw, "shuffle_on_load", source_path, "config.robot_hand", bool),
        beats_per_loop=_require_typed_value(robot_raw, "beats_per_loop", source_path, "config.robot_hand", float),
        clip_cache_size=_require_typed_value(robot_raw, "clip_cache_size", source_path, "config.robot_hand", int),
        render_batch=_require_typed_value(robot_raw, "render_batch", source_path, "config.robot_hand", int),
        bpm_smoothing=_require_typed_value(robot_raw, "bpm_smoothing", source_path, "config.robot_hand", float),
        sync_strength=_require_typed_value(robot_raw, "sync_strength", source_path, "config.robot_hand", float),
        udp_host=_require_typed_value(robot_raw, "udp_host", source_path, "config.robot_hand", str),
        udp_port=_require_typed_value(robot_raw, "udp_port", source_path, "config.robot_hand", int),
        notify_host=_require_typed_value(robot_raw, "notify_host", source_path, "config.robot_hand", str),
        notify_port=_require_typed_value(robot_raw, "notify_port", source_path, "config.robot_hand", int),
        status_hide_ms=_require_typed_value(robot_raw, "status_hide_ms", source_path, "config.robot_hand", int),
        resize_debounce_ms=_require_typed_value(robot_raw, "resize_debounce_ms", source_path, "config.robot_hand", int),
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
        bookmarks_folder_name=str(browser_values.get("bookmarks_folder_name", "Fun Time Favs")),
        open_count=int(browser_values.get("open_count", 10)),
    )


def load_config(config_path: str | Path | None = None) -> ProjectConfig:
    path = _resolve_config_path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        raw: dict[str, Any] = json.load(fp)

    paths_raw = _require_dict(raw, "paths", path)
    controller_raw = _require_dict(raw, "controller", path)
    broker_raw = _require_dict(raw, "broker", path)
    robot_raw = _require_dict(raw, "robot_hand", path)
    audio_raw = _require_dict(raw, "audio_companion", path)
    browser_raw = _require_optional_dict(raw, "random_favs_browser", path)
    if browser_raw is None:
        browser_raw = _require_optional_dict(raw, "chrome_overlay", path)

    return ProjectConfig(
        project_dir=PROJECT_DIR,
        config_path=path,
        paths=_load_paths_config(paths_raw, path),
        controller=_load_controller_config(controller_raw, path),
        broker=_load_broker_config(broker_raw, path),
        robot_hand=_load_robot_hand_config(robot_raw, path),
        audio_companion=_load_audio_companion_config(audio_raw, path),
        random_favs_browser=_load_random_favs_browser_config(browser_raw),
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
