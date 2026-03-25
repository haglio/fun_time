from __future__ import annotations

import argparse
import configparser
from pathlib import Path

from .controller_robot_hand import build_robot_hand_plan
from .controller_vlc_actions import ensure_playback_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Fun Time Robot Hand action plans.")
    parser.add_argument("action", choices=("sync-state", "toggle-enabled", "apply-toggle-enabled"))
    parser.add_argument("--robot-hand-mode-on", required=True)
    parser.add_argument("--enabled", required=True)
    parser.add_argument("--mode-state-on", required=True)
    parser.add_argument("--omni-paused", required=True)
    parser.add_argument("--plan-file", required=True)
    parser.add_argument("--enabled-file", default="")
    parser.add_argument("--paused-file", default="")
    parser.add_argument("--audio-paused-file", default="")
    parser.add_argument("--primary-port", type=int)
    parser.add_argument("--password", default="")
    return parser


def _to_bool(value: str) -> bool:
    return value.strip() not in {"", "0", "false", "False"}


def _write_state_file(path: str, value: bool) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("1" if value else "0", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = build_robot_hand_plan(
        args.action.removeprefix("apply-"),
        robot_hand_mode_on=_to_bool(args.robot_hand_mode_on),
        enabled=_to_bool(args.enabled),
        mode_state_on=_to_bool(args.mode_state_on),
        omni_paused=_to_bool(args.omni_paused),
    )

    if args.action == "apply-toggle-enabled":
        if not all((args.enabled_file, args.paused_file, args.audio_paused_file, args.primary_port, args.password)):
            raise ValueError("apply-toggle-enabled requires state-file paths, primary port, and password")
        if plan.write_enabled:
            _write_state_file(args.enabled_file, plan.enabled_value)
        if plan.enforce_outputs:
            _write_state_file(args.paused_file, not plan.enforce_active)
            _write_state_file(args.audio_paused_file, not plan.enforce_active)
            if not ensure_playback_state(args.primary_port, args.password, should_play=not plan.enforce_active):
                return 4

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["plan"] = {
        "write_enabled": "1" if plan.write_enabled else "0",
        "enabled_value": "1" if plan.enabled_value else "0",
        "next_robot_hand_mode": "1" if plan.next_robot_hand_mode else "0",
        "enforce_outputs": "1" if plan.enforce_outputs else "0",
        "enforce_active": "1" if plan.enforce_active else "0",
        "is_transition": "1" if plan.is_transition else "0",
        "log_message": plan.log_message,
    }
    plan_file = Path(args.plan_file)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    with plan_file.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
