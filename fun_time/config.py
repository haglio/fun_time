from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "fun_time_config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "paths": {
        "vlc_exe": "C:/Program Files/VideoLAN/VLC/vlc.exe",
        "mfp_exe": "C:/Program Files/MultiFunPlayer-1.33.9-patreon/MultiFunPlayer.exe",
        "ahk_exe": "C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe",
        "python_exe": "C:/Users/Example/miniconda3/pythonw.exe",
        "winston_dir": "C:/path/to/suite-root/videos/videos/2D/winston/3_good_to_go",
        "portrait_dir": "C:/path/to/suite-root/videos/videos/2D/AI/2_outbox/upscaled_by_orientation/portrait",
        "landscape_dir": "C:/path/to/suite-root/videos/videos/2D/AI/2_outbox/upscaled_by_orientation/landscape",
        "weird_dir": "C:/path/to/suite-root/videos/videos/2D/AI/2_outbox/kinda_weird",
        "clips_dir": "fun_time/robot_hand/clips",
        "audio_dir": "fun_time/robot_hand/audio",
        "favs_file": "favs.csv",
        "state_dir": "state",
    },
    "controller": {
        "vlc2_http_port": 8091,
        "vlc3_http_port": 8092,
        "layout": {
            "primary_monitor": 1,
            "secondary_monitor": 2,
            "primary_top_ratio": 0.7272727273,
            "landscape_width_ratio": 0.6666666667,
            "mfp_width_ratio": 0.9,
            "mfp_height_ratio": 0.6,
        },
    },
    "broker": {
        "virtual_port": "COM15",
        "real_port": "COM4",
        "baud": 115200,
        "udp_host": "127.0.0.1",
        "udp_port": 50555,
        "auto_stale_timeout": 8.0,
    },
    "robot_hand": {
        "beats_per_loop": 1.0,
        "clip_cache_size": 2,
        "render_batch": 6,
        "bpm_smoothing": 0.14,
        "sync_strength": 0.35,
        "udp_host": "127.0.0.1",
        "udp_port": 50555,
        "notify_host": "127.0.0.1",
        "notify_port": 50556,
        "status_hide_ms": 1200,
        "resize_debounce_ms": 120,
    },
    "audio_companion": {
        "host": "127.0.0.1",
        "port": 50556,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_path(project_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (project_dir / path).resolve()


@dataclass(frozen=True)
class PathsConfig:
    vlc_exe: Path
    mfp_exe: Path
    ahk_exe: Path
    python_exe: Path
    winston_dir: Path
    portrait_dir: Path
    landscape_dir: Path
    weird_dir: Path
    clips_dir: Path
    audio_dir: Path
    favs_file: Path
    state_dir: Path


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
class ProjectConfig:
    project_dir: Path
    config_path: Path
    paths: PathsConfig
    controller: ControllerConfig
    broker: BrokerConfig
    robot_hand: RobotHandConfig
    audio_companion: AudioCompanionConfig

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
    def logs_dir(self) -> Path:
        return self.paths.state_dir

    def log_file(self, name: str) -> Path:
        return self.logs_dir / f"{name}.log"


def load_config(config_path: str | Path | None = None) -> ProjectConfig:
    path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = (PROJECT_DIR / path).resolve()

    raw = deepcopy(DEFAULT_CONFIG)
    if path.exists():
        with path.open("r", encoding="utf-8") as fp:
            loaded = json.load(fp)
        raw = _deep_merge(raw, loaded)
    elif config_path is not None:
        raise FileNotFoundError(f"Config file not found: {path}")

    paths_raw = raw["paths"]
    controller_raw = raw["controller"]
    layout_raw = controller_raw["layout"]
    broker_raw = raw["broker"]
    robot_raw = raw["robot_hand"]
    audio_raw = raw["audio_companion"]

    paths = PathsConfig(
        vlc_exe=_resolve_path(PROJECT_DIR, paths_raw["vlc_exe"]),
        mfp_exe=_resolve_path(PROJECT_DIR, paths_raw["mfp_exe"]),
        ahk_exe=_resolve_path(PROJECT_DIR, paths_raw["ahk_exe"]),
        python_exe=_resolve_path(PROJECT_DIR, paths_raw["python_exe"]),
        winston_dir=_resolve_path(PROJECT_DIR, paths_raw["winston_dir"]),
        portrait_dir=_resolve_path(PROJECT_DIR, paths_raw["portrait_dir"]),
        landscape_dir=_resolve_path(PROJECT_DIR, paths_raw["landscape_dir"]),
        weird_dir=_resolve_path(PROJECT_DIR, paths_raw["weird_dir"]),
        clips_dir=_resolve_path(PROJECT_DIR, paths_raw["clips_dir"]),
        audio_dir=_resolve_path(PROJECT_DIR, paths_raw["audio_dir"]),
        favs_file=_resolve_path(PROJECT_DIR, paths_raw["favs_file"]),
        state_dir=_resolve_path(PROJECT_DIR, paths_raw["state_dir"]),
    )

    controller = ControllerConfig(
        vlc2_http_port=int(controller_raw["vlc2_http_port"]),
        vlc3_http_port=int(controller_raw["vlc3_http_port"]),
        layout=LayoutConfig(
            primary_monitor=int(layout_raw["primary_monitor"]),
            secondary_monitor=int(layout_raw["secondary_monitor"]),
            primary_top_ratio=float(layout_raw["primary_top_ratio"]),
            landscape_width_ratio=float(layout_raw["landscape_width_ratio"]),
            mfp_width_ratio=float(layout_raw["mfp_width_ratio"]),
            mfp_height_ratio=float(layout_raw["mfp_height_ratio"]),
        ),
    )

    broker = BrokerConfig(
        virtual_port=str(broker_raw["virtual_port"]),
        real_port=str(broker_raw["real_port"]),
        baud=int(broker_raw["baud"]),
        udp_host=str(broker_raw["udp_host"]),
        udp_port=int(broker_raw["udp_port"]),
        auto_stale_timeout=float(broker_raw["auto_stale_timeout"]),
    )

    robot_hand = RobotHandConfig(
        beats_per_loop=float(robot_raw["beats_per_loop"]),
        clip_cache_size=int(robot_raw["clip_cache_size"]),
        render_batch=int(robot_raw["render_batch"]),
        bpm_smoothing=float(robot_raw["bpm_smoothing"]),
        sync_strength=float(robot_raw["sync_strength"]),
        udp_host=str(robot_raw["udp_host"]),
        udp_port=int(robot_raw["udp_port"]),
        notify_host=str(robot_raw["notify_host"]),
        notify_port=int(robot_raw["notify_port"]),
        status_hide_ms=int(robot_raw["status_hide_ms"]),
        resize_debounce_ms=int(robot_raw["resize_debounce_ms"]),
    )

    audio_companion = AudioCompanionConfig(
        host=str(audio_raw["host"]),
        port=int(audio_raw["port"]),
    )

    return ProjectConfig(
        project_dir=PROJECT_DIR,
        config_path=path,
        paths=paths,
        controller=controller,
        broker=broker,
        robot_hand=robot_hand,
        audio_companion=audio_companion,
    )