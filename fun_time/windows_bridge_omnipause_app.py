from __future__ import annotations

import argparse
import configparser
from pathlib import Path

from .windows_bridge_omnipause import build_omnipause_plan
from .windows_bridge_vlc_actions import ensure_playback_state, vlc_http_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Fun Time OmniPause action plans.")
    parser.add_argument("action", choices=("toggle", "enter", "leave", "apply-enter", "apply-leave"))
    parser.add_argument("--omni-paused", required=True)
    parser.add_argument("--robot-hand-mode-on", required=True)
    parser.add_argument("--skip-primary-resume", required=True)
    parser.add_argument("--plan-file", required=True)
    parser.add_argument("--portrait-port", type=int)
    parser.add_argument("--landscape-port", type=int)
    parser.add_argument("--primary-port", type=int)
    parser.add_argument("--password", default="")
    parser.add_argument("--robot-hand-paused-file", default="")
    parser.add_argument("--audio-paused-file", default="")
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
    plan = build_omnipause_plan(
        args.action.removeprefix("apply-"),
        omni_paused=_to_bool(args.omni_paused),
        robot_hand_mode_on=_to_bool(args.robot_hand_mode_on),
        skip_primary_resume=_to_bool(args.skip_primary_resume),
    )

    if args.action in {"apply-enter", "apply-leave"}:
        if not all((args.portrait_port, args.landscape_port, args.primary_port, args.password)):
            raise ValueError("apply-enter/apply-leave require ports and password")
        if not vlc_http_cmd(args.portrait_port, "pl_pause", args.password):
            return 4
        if not vlc_http_cmd(args.landscape_port, "pl_pause", args.password):
            return 5
        if plan.robot_hand_branch:
            if args.action == "apply-enter":
                _write_state_file(args.robot_hand_paused_file, True)
                _write_state_file(args.audio_paused_file, True)
            else:
                _write_state_file(args.robot_hand_paused_file, False)
                _write_state_file(args.audio_paused_file, False)
        elif args.action == "apply-enter":
            if not ensure_playback_state(args.primary_port, args.password, should_play=False):
                return 6
        elif plan.resume_primary_playback:
            if not ensure_playback_state(args.primary_port, args.password, should_play=True):
                return 7

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["plan"] = {
        "action": plan.action,
        "next_omni_paused": "1" if plan.next_omni_paused else "0",
        "robot_hand_branch": "1" if plan.robot_hand_branch else "0",
        "resume_primary_playback": "1" if plan.resume_primary_playback else "0",
        "log_message": plan.log_message,
    }
    plan_file = Path(args.plan_file)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    with plan_file.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
