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
    primary_monitor: int
    secondary_monitor: int
    primary_top_ratio: float
    landscape_width_ratio: float
    mfp_width_ratio: float
    mfp_height_ratio: float


@dataclass(frozen=True)
class ControllerConfig:
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
class ChromeOverlayConfig:
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
    chrome_overlay: ChromeOverlayConfig

    @property
    def robot_hand_mode_file(self) -> Path:
        return self.paths.state_dir / "robot_hand_mode.txt"

    @property
    def robot_hand_cmd_file(self) -> Path:
        return self.paths.state_dir / "robot_hand_cmd.txt"

    @property
    def broker_cmd_file(self) -> Path:
        return self.paths.state_dir / "broker_cmd.txt"

    @property
    def audio_cmd_file(self) -> Path:
        return self.paths.state_dir / "audio_cmd.txt"

    @property
    def chrome_overlay_manifest_file(self) -> Path:
        return self.paths.state_dir / "chrome_overlay_urls.txt"

    @property
    def logs_dir(self) -> Path:
        return self.paths.state_dir

    def log_file(self, name: str) -> Path:
        return self.logs_dir / f"{name}.log"


def load_config(config_path: str | Path | None = None) -> ProjectConfig:
    path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = (PROJECT_DIR / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        raw: dict[str, Any] = json.load(fp)

    paths_raw = _require_dict(raw, "paths", path)
    controller_raw = _require_dict(raw, "controller", path)
    layout_raw = _require_dict(controller_raw, "layout", path, "config.controller")
    broker_raw = _require_dict(raw, "broker", path)
    robot_raw = _require_dict(raw, "robot_hand", path)
    audio_raw = _require_dict(raw, "audio_companion", path)
    chrome_raw = raw.get("chrome_overlay")
    if chrome_raw is not None and not isinstance(chrome_raw, dict):
        raise TypeError(f"Expected object for config section: chrome_overlay (in {path})")
    primary_vlc_dirs_raw = _require_value(paths_raw, "primary_vlc_dirs", path, "config.paths")
    if not isinstance(primary_vlc_dirs_raw, list):
        raise TypeError("paths.primary_vlc_dirs must be a list of folder paths")
    if not primary_vlc_dirs_raw:
        raise ValueError("paths.primary_vlc_dirs must include at least one folder path")
    primary_vlc_dirs = tuple(_resolve_path(PROJECT_DIR, str(path)) for path in primary_vlc_dirs_raw)
    portrait_dirs = _load_dir_list(paths_raw, "portrait_dirs", "portrait_dir", path)
    landscape_dirs = _load_dir_list(paths_raw, "landscape_dirs", "landscape_dir", path)

    paths = PathsConfig(
        vlc_exe=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "vlc_exe", path, "config.paths")),
        mfp_exe=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "mfp_exe", path, "config.paths")),
        ahk_exe=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "ahk_exe", path, "config.paths")),
        python_exe=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "python_exe", path, "config.paths")),
        primary_vlc_dirs=primary_vlc_dirs,
        portrait_dirs=portrait_dirs,
        landscape_dirs=landscape_dirs,
        weird_dir=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "weird_dir", path, "config.paths")),
        clips_dir=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "clips_dir", path, "config.paths")),
        audio_dir=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "audio_dir", path, "config.paths")),
        favs_file=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "favs_file", path, "config.paths")),
        state_dir=_resolve_path(PROJECT_DIR, _require_value(paths_raw, "state_dir", path, "config.paths")),
    )

    controller = ControllerConfig(
        vlc2_http_port=int(_require_value(controller_raw, "vlc2_http_port", path, "config.controller")),
        vlc3_http_port=int(_require_value(controller_raw, "vlc3_http_port", path, "config.controller")),
        layout=LayoutConfig(
            primary_monitor=int(_require_value(layout_raw, "primary_monitor", path, "config.controller.layout")),
            secondary_monitor=int(_require_value(layout_raw, "secondary_monitor", path, "config.controller.layout")),
            primary_top_ratio=float(_require_value(layout_raw, "primary_top_ratio", path, "config.controller.layout")),
            landscape_width_ratio=float(_require_value(layout_raw, "landscape_width_ratio", path, "config.controller.layout")),
            mfp_width_ratio=float(_require_value(layout_raw, "mfp_width_ratio", path, "config.controller.layout")),
            mfp_height_ratio=float(_require_value(layout_raw, "mfp_height_ratio", path, "config.controller.layout")),
        ),
    )

    broker = BrokerConfig(
        virtual_port=str(_require_value(broker_raw, "virtual_port", path, "config.broker")),
        real_port=str(_require_value(broker_raw, "real_port", path, "config.broker")),
        baud=int(_require_value(broker_raw, "baud", path, "config.broker")),
        udp_host=str(_require_value(broker_raw, "udp_host", path, "config.broker")),
        udp_port=int(_require_value(broker_raw, "udp_port", path, "config.broker")),
        auto_stale_timeout=float(_require_value(broker_raw, "auto_stale_timeout", path, "config.broker")),
    )

    robot_hand = RobotHandConfig(
        shuffle_on_load=bool(_require_value(robot_raw, "shuffle_on_load", path, "config.robot_hand")),
        beats_per_loop=float(_require_value(robot_raw, "beats_per_loop", path, "config.robot_hand")),
        clip_cache_size=int(_require_value(robot_raw, "clip_cache_size", path, "config.robot_hand")),
        render_batch=int(_require_value(robot_raw, "render_batch", path, "config.robot_hand")),
        bpm_smoothing=float(_require_value(robot_raw, "bpm_smoothing", path, "config.robot_hand")),
        sync_strength=float(_require_value(robot_raw, "sync_strength", path, "config.robot_hand")),
        udp_host=str(_require_value(robot_raw, "udp_host", path, "config.robot_hand")),
        udp_port=int(_require_value(robot_raw, "udp_port", path, "config.robot_hand")),
        notify_host=str(_require_value(robot_raw, "notify_host", path, "config.robot_hand")),
        notify_port=int(_require_value(robot_raw, "notify_port", path, "config.robot_hand")),
        status_hide_ms=int(_require_value(robot_raw, "status_hide_ms", path, "config.robot_hand")),
        resize_debounce_ms=int(_require_value(robot_raw, "resize_debounce_ms", path, "config.robot_hand")),
    )

    audio_companion = AudioCompanionConfig(
        host=str(_require_value(audio_raw, "host", path, "config.audio_companion")),
        port=int(_require_value(audio_raw, "port", path, "config.audio_companion")),
    )

    default_user_data_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    chrome_overlay = ChromeOverlayConfig(
        enabled=bool((chrome_raw or {}).get("enabled", False)),
        shortcut_path=_resolve_path(
            PROJECT_DIR,
            str((chrome_raw or {}).get("shortcut_path", "Blair Chrome.lnk")),
        ),
        user_data_dir=_resolve_path(
            PROJECT_DIR,
            str((chrome_raw or {}).get("user_data_dir", default_user_data_dir)),
        ),
        profile_name=str((chrome_raw or {}).get("profile_name", "Blair")),
        bookmarks_folder_name=str((chrome_raw or {}).get("bookmarks_folder_name", "Fun Time Favs")),
        open_count=int((chrome_raw or {}).get("open_count", 10)),
    )

    return ProjectConfig(
        project_dir=PROJECT_DIR,
        config_path=path,
        paths=paths,
        controller=controller,
        broker=broker,
        robot_hand=robot_hand,
        audio_companion=audio_companion,
        chrome_overlay=chrome_overlay,
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
