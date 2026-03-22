from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
from pathlib import Path

from .config import load_config
from .logging_utils import configure_logging, install_exception_logging


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Launch the Fun Time controller stack.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--check", action="store_true", help="Validate config and exit.")
    return ap


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")


def require_dir(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Missing directory: {path}")


def ensure_runtime_files(config) -> None:
    config.paths.state_dir.mkdir(parents=True, exist_ok=True)
    config.paths.weird_dir.mkdir(parents=True, exist_ok=True)
    config.paths.favs_file.parent.mkdir(parents=True, exist_ok=True)
    config.paths.favs_file.touch(exist_ok=True)


def validate_config(config) -> None:
    require_file(config.paths.vlc_exe)
    require_file(config.paths.mfp_exe)
    require_file(config.paths.ahk_exe)
    require_file(config.paths.python_exe)
    for primary_vlc_dir in config.paths.primary_vlc_dirs:
        require_dir(primary_vlc_dir)
    for portrait_dir in config.paths.portrait_dirs:
        require_dir(portrait_dir)
    for landscape_dir in config.paths.landscape_dirs:
        require_dir(landscape_dir)
    require_dir(config.paths.clips_dir)
    require_dir(config.paths.audio_dir)
    require_file(config.project_dir / "controller.ahk")
    require_file(config.project_dir / "fun_time" / "broker_app.py")
    require_file(config.project_dir / "fun_time" / "robot_hand" / "app.py")
    require_file(config.project_dir / "fun_time" / "audio_companion_app.py")


def build_controller_args(config, vlc_http_pass: str) -> list[str]:
    layout = config.controller.layout
    primary_vlc_dirs_arg = "|".join(str(path) for path in config.paths.primary_vlc_dirs)
    portrait_dirs_arg = "|".join(str(path) for path in config.paths.portrait_dirs)
    landscape_dirs_arg = "|".join(str(path) for path in config.paths.landscape_dirs)
    return [
        str(config.paths.vlc_exe),
        str(config.paths.mfp_exe),
        primary_vlc_dirs_arg,
        portrait_dirs_arg,
        landscape_dirs_arg,
        str(config.paths.weird_dir),
        str(config.paths.favs_file),
        str(config.controller.vlc2_http_port),
        str(config.controller.vlc3_http_port),
        vlc_http_pass,
        str(config.paths.python_exe),
        "fun_time.robot_hand.app",
        str(config.paths.clips_dir),
        "fun_time.audio_companion_app",
        str(config.paths.audio_dir),
        str(config.robot_hand_mode_file),
        str(config.robot_hand_cmd_file),
        str(config.broker_cmd_file),
        str(config.audio_cmd_file),
        str(layout.primary_monitor),
        str(layout.secondary_monitor),
        str(layout.primary_top_ratio),
        str(layout.landscape_width_ratio),
        str(layout.mfp_width_ratio),
        str(layout.mfp_height_ratio),
        str(config.log_file("controller")),
        str(config.config_path),
    ]


def run_controller(config, logger) -> int:
    ahk_script = config.project_dir / "controller.ahk"
    vlc_http_pass = f"fun_time_{secrets.token_hex(6)}"
    command = [str(config.paths.ahk_exe), str(ahk_script), *build_controller_args(config, vlc_http_pass)]
    logger.info("Launching AutoHotkey controller using config %s", config.config_path)
    logger.info("VLC HTTP ports: portrait=%s landscape=%s", config.controller.vlc2_http_port, config.controller.vlc3_http_port)

    result = subprocess.run(command, cwd=config.project_dir, check=False)
    logger.info("Controller exited with code %s", result.returncode)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    logger = configure_logging("fun_time.orchestrator", config.log_file("orchestrator"), console=True)
    install_exception_logging(logger)

    logger.info("Loaded config from %s", config.config_path)
    ensure_runtime_files(config)
    validate_config(config)

    if args.check:
        logger.info("Config validation succeeded")
        return 0

    return run_controller(config, logger)


if __name__ == "__main__":
    raise SystemExit(main())
