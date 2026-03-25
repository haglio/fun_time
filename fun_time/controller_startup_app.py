from __future__ import annotations

import argparse

from .controller_startup import prepare_random_favs_browser_manifest, restart_broker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Fun Time controller startup helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    restart = subparsers.add_parser("restart-broker", help="Restart the broker tray and service.")
    restart.add_argument("--project-dir", required=True)

    browser = subparsers.add_parser(
        "prepare-random-favs-browser-manifest",
        help="Write the Random Favs Browser manifest.",
    )
    browser.add_argument("--config", required=True)
    browser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "restart-broker":
        restart_broker(args.project_dir)
        return 0

    prepare_random_favs_browser_manifest(args.config, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
