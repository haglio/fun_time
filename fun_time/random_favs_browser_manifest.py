from __future__ import annotations

from pathlib import Path

from .config import load_config
from .content import load_web_providers
from .random_favs_browser import build_manifest, write_manifest
from .rfb_tab_page import tabs_dir, write_tab_pages


def prepare_random_favs_browser_manifest(config_path: str | Path, output_path: str | Path) -> None:
    config = load_config(config_path)
    profile_directory, targets = build_manifest(config, load_web_providers())
    urls = (
        write_tab_pages(
            tabs_dir(config.paths.state_dir), targets, loopback_port=config.loopback_port)
        if config.random_favs_browser.lazy_load
        else [target.url for target in targets]
    )
    write_manifest(Path(output_path), profile_directory, urls)
