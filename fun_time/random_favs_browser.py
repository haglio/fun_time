from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from .config import load_config


def resolve_profile_directory(user_data_dir: Path, profile_name: str) -> str:
    local_state_path = user_data_dir / "Local State"
    if not local_state_path.is_file():
        return ""

    with local_state_path.open("r", encoding="utf-8") as fh:
        local_state = json.load(fh)

    info_cache = local_state.get("profile", {}).get("info_cache", {})
    if not isinstance(info_cache, dict):
        return ""

    for directory_name, entry in info_cache.items():
        if isinstance(entry, dict) and entry.get("name") == profile_name:
            return directory_name
    return ""


def load_folder_urls(bookmarks_path: Path, folder_name: str) -> list[str]:
    if not bookmarks_path.is_file():
        return []

    with bookmarks_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    roots = data.get("roots", {})
    for root in roots.values():
        if not isinstance(root, dict):
            continue
        found = _find_folder_urls(root, folder_name)
        if found is not None:
            return found
    return []


def choose_random_urls(urls: list[str], count: int, rng: random.Random | None = None) -> list[str]:
    if not urls or count <= 0:
        return []
    chooser = rng or random.SystemRandom()
    if len(urls) <= count:
        picked = list(urls)
        chooser.shuffle(picked)
        return picked
    return chooser.sample(urls, count)


def build_manifest(config_path: str | Path | None = None) -> tuple[str, list[str]]:
    config = load_config(config_path)
    browser = config.random_favs_browser
    if not browser.enabled:
        return "", []

    profile_directory = resolve_profile_directory(browser.user_data_dir, browser.profile_name)
    if not profile_directory:
        return "", []

    bookmarks_path = browser.user_data_dir / profile_directory / "Bookmarks"
    urls = load_folder_urls(bookmarks_path, browser.bookmarks_folder_name)
    return profile_directory, choose_random_urls(urls, browser.open_count)


def write_manifest(output_path: Path, profile_directory: str, urls: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [profile_directory, *urls]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_folder_urls(node: dict, folder_name: str) -> list[str] | None:
    if node.get("type") == "folder" and node.get("name") == folder_name:
        children = node.get("children", [])
        if not isinstance(children, list):
            return []
        urls: list[str] = []
        for child in children:
            if isinstance(child, dict) and child.get("type") == "url":
                url = child.get("url")
                if isinstance(url, str) and url:
                    urls.append(url)
        return urls

    children = node.get("children", [])
    if not isinstance(children, list):
        return None
    for child in children:
        if isinstance(child, dict):
            found = _find_folder_urls(child, folder_name)
            if found is not None:
                return found
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a Random Favs Browser launch manifest for Fun Time.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--output", required=True, help="Output manifest file path.")
    args = ap.parse_args(argv)

    profile_directory, urls = build_manifest(args.config)
    write_manifest(Path(args.output), profile_directory, urls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
