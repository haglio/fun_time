from __future__ import annotations

import argparse
import configparser
from pathlib import Path

from .controller_lock import build_lock_plan
from .controller_vlc_actions import set_repeat_mode, vlc_http_cmd
from .media_actions import ensure_in_favs, move_to_weird, remove_from_favs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Fun Time lock/discard action plans.")
    parser.add_argument(
        "action",
        choices=(
            "toggle-lock",
            "cancel-lock",
            "discard",
            "apply-toggle-lock",
            "apply-cancel-lock",
            "apply-discard",
        ),
    )
    parser.add_argument("--which", type=int, required=True)
    parser.add_argument("--locked", required=True)
    parser.add_argument("--current-path", default="")
    parser.add_argument("--plan-file", required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--password", default="")
    parser.add_argument("--favs-file", default="")
    parser.add_argument("--weird-dir", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan_action = args.action.removeprefix("apply-")
    plan = build_lock_plan(
        plan_action,
        which=args.which,
        locked=args.locked.strip() not in {"", "0", "false", "False"},
        current_path=args.current_path,
    )

    if args.action.startswith("apply-"):
        if args.port is None or not args.password:
            raise ValueError("apply-* actions require --port and --password")
        if plan.repeat_mode and not set_repeat_mode(args.port, args.password, plan.repeat_mode):
            return 4
        if plan.ensure_in_favs and args.favs_file:
            ensure_in_favs(Path(args.favs_file), args.current_path)
        if plan.remove_from_favs and args.favs_file:
            remove_from_favs(Path(args.favs_file), args.current_path)
        if plan.advance_playlist and not vlc_http_cmd(args.port, "pl_next", args.password):
            return 5
        if plan.move_to_weird and args.weird_dir:
            move_to_weird(Path(args.weird_dir), Path(args.current_path))

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["plan"] = {
        "next_locked": "1" if plan.next_locked else "0",
        "repeat_mode": plan.repeat_mode,
        "ensure_in_favs": "1" if plan.ensure_in_favs else "0",
        "remove_from_favs": "1" if plan.remove_from_favs else "0",
        "advance_playlist": "1" if plan.advance_playlist else "0",
        "move_to_weird": "1" if plan.move_to_weird else "0",
        "log_message": plan.log_message,
    }
    plan_file = Path(args.plan_file)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    with plan_file.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
