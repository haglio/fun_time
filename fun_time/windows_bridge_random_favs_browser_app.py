from __future__ import annotations

import argparse
import base64
import configparser
from pathlib import Path

from .windows_bridge_random_favs_browser import build_random_favs_browser_launch_plan, launch_random_favs_browser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or launch a Random Favs Browser command.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    write_plan = subparsers.add_parser("write-plan", help="Write a browser launch plan file.")
    write_plan.add_argument("--manifest-file", required=True)
    write_plan.add_argument("--shortcut-target", required=True)
    write_plan.add_argument("--shortcut-work-dir", default="")
    write_plan.add_argument("--shortcut-args-b64", default="")
    write_plan.add_argument("--plan-file", required=True)

    launch = subparsers.add_parser("launch", help="Launch the browser directly.")
    launch.add_argument("--manifest-file", required=True)
    launch.add_argument("--shortcut-target", required=True)
    launch.add_argument("--shortcut-work-dir", default="")
    launch.add_argument("--shortcut-args-b64", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    shortcut_args = base64.b64decode(args.shortcut_args_b64.encode("ascii")).decode("utf-8") if args.shortcut_args_b64 else ""
    if args.command == "write-plan":
        plan = build_random_favs_browser_launch_plan(
            args.manifest_file,
            shortcut_target=args.shortcut_target,
            shortcut_work_dir=args.shortcut_work_dir,
            shortcut_args=shortcut_args,
        )

        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser["plan"] = {
            "should_launch": "1" if plan.should_launch else "0",
            "cmd": plan.cmd,
            "work_dir": plan.work_dir,
        }
        plan_file = Path(args.plan_file)
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        with plan_file.open("w", encoding="utf-8") as fh:
            parser.write(fh)
        return 0

    plan = launch_random_favs_browser(
        args.manifest_file,
        shortcut_target=args.shortcut_target,
        shortcut_work_dir=args.shortcut_work_dir,
        shortcut_args=shortcut_args,
    )
    if not plan.should_launch or not plan.cmd:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
