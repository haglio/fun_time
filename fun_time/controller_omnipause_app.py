from __future__ import annotations

import argparse
import configparser
from pathlib import Path

from .controller_omnipause import build_omnipause_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Fun Time OmniPause action plans.")
    parser.add_argument("action", choices=("toggle", "enter", "leave"))
    parser.add_argument("--omni-paused", required=True)
    parser.add_argument("--robot-hand-mode-on", required=True)
    parser.add_argument("--skip-primary-resume", required=True)
    parser.add_argument("--plan-file", required=True)
    return parser


def _to_bool(value: str) -> bool:
    return value.strip() not in {"", "0", "false", "False"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = build_omnipause_plan(
        args.action,
        omni_paused=_to_bool(args.omni_paused),
        robot_hand_mode_on=_to_bool(args.robot_hand_mode_on),
        skip_primary_resume=_to_bool(args.skip_primary_resume),
    )

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
