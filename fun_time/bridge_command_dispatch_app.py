"""CLI entrypoint for the bridge command dispatcher.

Called by the AHK windows bridge to dispatch a dashboard or hotkey command.
Replaces the many separate subprocess calls (lock, modes, runtime flow, etc.)
with a single consolidated Python call per command.
"""
from __future__ import annotations

import argparse
import configparser
import logging
from pathlib import Path

from .bridge_command_dispatch import (
    BridgeConfig,
    BridgeState,
    WindowOp,
    dispatch_command,
)
from .windows_bridge_dashboard_bridge import write_dashboard_snapshot
from .windows_bridge_dispatch_loop import write_shared_state
from .windows_bridge_runtime_flow import read_flag_file


def _to_bool(value: str) -> bool:
    return value.strip() not in {"", "0", "false", "False"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Dispatch a bridge command.")
    ap.add_argument("command", help="The command to dispatch (e.g. portrait_lock)")
    ap.add_argument("--result-file", required=True, help="Path to write result INI file")
    ap.add_argument("--config-path", required=True, help="Path to fun_time config JSON")
    ap.add_argument("--vlc-password", required=True)
    ap.add_argument("--locked2", default="0")
    ap.add_argument("--locked3", default="0")
    ap.add_argument("--robot-hand-mode", default="0")
    ap.add_argument("--f-mode-enabled", default="0")
    ap.add_argument("--omni-paused", default="0")
    ap.add_argument("--shared-state-file", default="")
    ap.add_argument("--dashboard-state-file", default="")
    ap.add_argument("--dashboard-enabled", default="0")
    ap.add_argument("--mfp-alive", default="0")
    return ap


def _build_config_from_fun_time_config(config_path: str, vlc_password: str) -> BridgeConfig:
    from .config import load_config

    cfg = load_config(config_path)
    return BridgeConfig(
        primary_port=cfg.controller.primary_vlc_http_port,
        portrait_port=cfg.controller.vlc2_http_port,
        landscape_port=cfg.controller.vlc3_http_port,
        vlc_password=vlc_password,
        favs_file=cfg.paths.favs_file,
        weird_dir=cfg.paths.weird_dir,
        state_dir=cfg.paths.state_dir,
        primary_sources="|".join(str(d) for d in cfg.paths.primary_vlc_dirs),
        portrait_sources="|".join(str(d) for d in cfg.paths.portrait_dirs),
        landscape_sources="|".join(str(d) for d in cfg.paths.landscape_dirs),
        robot_hand_enabled_file=cfg.robot_hand_enabled_file,
        robot_hand_mode_file=cfg.robot_hand_mode_file,
        robot_hand_cmd_file=cfg.paths.state_dir / "robot_hand_cmd.txt",
        robot_hand_paused_file=cfg.paths.state_dir / "robot_hand_paused.txt",
        audio_paused_file=cfg.paths.state_dir / "audio_paused.txt",
        dashboard_state_file=cfg.paths.state_dir / "dashboard_state.ini",
    )


class _LogCapture(logging.Handler):
    """Capture log messages during dispatch for forwarding to AHK."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord):
        self.messages.append(record.getMessage())


def _write_result(path: Path, state: BridgeState, ops: list[WindowOp], log_messages: list[str] | None = None) -> None:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["state"] = {
        "locked2": "1" if state.locked2 else "0",
        "locked3": "1" if state.locked3 else "0",
        "robot_hand_mode": "1" if state.robot_hand_mode else "0",
        "f_mode_enabled": "1" if state.f_mode_enabled else "0",
        "omni_paused": "1" if state.omni_paused else "0",
    }
    if log_messages:
        parser["state"]["log_message"] = " | ".join(log_messages)
    parser["ops"] = {"count": str(len(ops))}
    for i, op in enumerate(ops):
        section = f"op_{i}"
        parser[section] = {"op": op.op}
        if op.pid:
            parser[section]["pid"] = str(op.pid)
        if op.title:
            parser[section]["title"] = op.title
        if op.key:
            parser[section]["key"] = op.key
        parser[section]["value"] = "1" if op.value else "0"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = _build_config_from_fun_time_config(args.config_path, args.vlc_password)
    state = BridgeState(
        locked2=_to_bool(args.locked2),
        locked3=_to_bool(args.locked3),
        robot_hand_mode=_to_bool(args.robot_hand_mode),
        f_mode_enabled=_to_bool(args.f_mode_enabled),
        omni_paused=_to_bool(args.omni_paused),
    )
    capture = _LogCapture()
    dispatch_logger = logging.getLogger("fun_time.bridge_command_dispatch")
    dispatch_logger.addHandler(capture)
    dispatch_logger.setLevel(logging.DEBUG)
    try:
        new_state, ops = dispatch_command(args.command, state, config)
    finally:
        dispatch_logger.removeHandler(capture)
    _write_result(Path(args.result_file), new_state, ops, capture.messages)
    if args.shared_state_file:
        write_shared_state(Path(args.shared_state_file), new_state)
    if _to_bool(args.dashboard_enabled) and args.dashboard_state_file:
        robot_link_enabled = read_flag_file(config.robot_hand_enabled_file, True)
        robot_hand_mode_on = read_flag_file(config.robot_hand_mode_file, False)
        write_dashboard_snapshot(
            args.dashboard_state_file,
            f_mode_enabled=new_state.f_mode_enabled,
            robot_link_enabled=robot_link_enabled,
            osr2_mode="auto" if robot_hand_mode_on else "controlled",
            mfp_alive=_to_bool(args.mfp_alive),
            primary_uses_robot_hand=new_state.robot_hand_mode and robot_link_enabled,
            portrait_locked=new_state.locked2,
            landscape_locked=new_state.locked3,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
