from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import url2pathname

from app_support import favorites

from fun_time.content import WebProvider, load_web_providers
from fun_time.lock import a_hosted_lock_holds, a_lock_holds, go_to_line
from fun_time.media_actions import make_web_url_from_path
from fun_time.media_metadata import metadata_path_for, recorded_for
from fun_time.origenerator_switches import RfbMode
from fun_time.shared_state import BridgeState
from fun_time.watch_stats import watch_stats_path
from tests.test_windows_bridge_dispatch_loop import make_config


def test_a_lock_with_nothing_on_screen_holds_nothing(tmp_path):
    config = make_config(tmp_path, regen_metadata_root=tmp_path / "metadata")

    assert a_lock_holds("", BridgeState(), config) == (BridgeState(), [])
    assert not config.regen_metadata_root.exists()
    assert not watch_stats_path(config.state_dir).exists()


def test_a_lock_marks_the_items_record_a_favorite_with_the_page_its_website_made_it_on(tmp_path):
    config = make_config(tmp_path, regen_metadata_root=tmp_path / "metadata")
    video = tmp_path / "videos" / "alpha" / "abc_123.mp4"
    providers = (WebProvider(marker="alpha", gallery_url="https://example.com/alpha/{id}"),)

    with patch("fun_time.lock.load_web_providers", return_value=providers):
        a_lock_holds(str(video), BridgeState(), config)

    record = recorded_for(video, config.regen_metadata_root)
    assert favorites.is_favorite(record)
    assert favorites.page_of(record) == "https://example.com/alpha/abc"


def _hosting(tmp_path):
    return make_config(tmp_path, regen_metadata_root=tmp_path / "metadata",
                       origenerator_enabled=True,
                       origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")


def _asked_of_the_hosted_app(config) -> list[str]:
    asked = config.origenerator_cmd_file
    return asked.read_text(encoding="utf-8").splitlines() if asked.exists() else []


def test_a_lock_the_hosted_app_tells_of_on_a_file_with_no_record_makes_none_and_shows_it_in_core(
        tmp_path):
    config = _hosting(tmp_path)
    video = str(tmp_path / "videos" / "made here" / "scene one.mp4")
    up = BridgeState(origenerator_ready=True)

    assert a_hosted_lock_holds(video, up, config) == (up, [])

    assert _asked_of_the_hosted_app(config) == [go_to_line(video)]
    assert not config.regen_metadata_root.exists()
    assert not watch_stats_path(config.state_dir).exists()


def test_a_hosted_lock_on_a_recorded_video_no_site_made_is_starred_and_shown_in_core_once(
        tmp_path):
    config = _hosting(tmp_path)
    video = tmp_path / "videos" / "made here" / "scene one.mp4"
    record = metadata_path_for(video, config.regen_metadata_root)
    record.parent.mkdir(parents=True)
    record.write_text("{}", encoding="utf-8")

    a_hosted_lock_holds(str(video), BridgeState(origenerator_ready=True), config)

    assert favorites.is_favorite(recorded_for(video, config.regen_metadata_root))
    assert _asked_of_the_hosted_app(config) == [go_to_line(str(video))]


def test_a_hosted_lock_on_a_sites_video_with_no_record_still_opens_the_sites_page(tmp_path):
    config = _hosting(tmp_path)
    video = rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4"

    _state, ops = a_hosted_lock_holds(video, BridgeState(origenerator_ready=True), config)

    assert [op.op for op in ops] == ["open_rfb_tab"]
    assert _asked_of_the_hosted_app(config) == []
    assert not config.regen_metadata_root.exists()


def test_a_hosted_lock_on_a_sites_video_puts_the_browser_back_over_origenerator(tmp_path):
    config = _hosting(tmp_path)
    video = rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4"

    state, ops = a_hosted_lock_holds(
        video, BridgeState(origenerator_ready=True, rfb_mode=RfbMode.ORIGENERATOR), config)

    assert state.rfb_mode is RfbMode.RFB
    assert [op.op for op in ops] == ["hide_window", "restack_rfb", "open_rfb_tab"]


def test_a_hosted_lock_asks_nothing_of_an_origenerator_still_starting(tmp_path):
    config = _hosting(tmp_path)

    a_hosted_lock_holds(str(tmp_path / "made here" / "scene one.mp4"), BridgeState(), config)

    assert _asked_of_the_hosted_app(config) == []


def _page_of(op_key: str) -> str:
    return Path(url2pathname(urlparse(op_key).path)).read_text(encoding="utf-8")


def test_a_lock_on_a_sites_video_opens_a_landing_page_for_the_sites_own_page(tmp_path):
    config = make_config(tmp_path)
    video = rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4"

    with patch("fun_time.lock.mark_favorite"):
        _state, [opened] = a_lock_holds(video, BridgeState(), config)

    assert opened.op == "open_rfb_tab"
    assert f'"{make_web_url_from_path(video, load_web_providers())}"' in _page_of(opened.key)


def test_locking_the_same_video_twice_reuses_one_landing_page(tmp_path):
    config = make_config(tmp_path)
    video = rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4"

    with patch("fun_time.lock.mark_favorite"):
        keys = [a_lock_holds(video, BridgeState(), config)[1][0].key for _ in range(2)]

    assert keys[0] == keys[1]
    assert len(list((config.state_dir / "rfb_tabs").glob("*.html"))) == 1


def test_a_lock_on_a_video_regen_made_opens_regens_page_for_it(tmp_path):
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    rel = Path("upscales") / "landscape" / "provider"
    video = media_root / rel / "vid_topaz.mp4"
    meta_file = metadata_root / "2D" / "AI" / rel / "vid_topaz.json"
    meta_file.parent.mkdir(parents=True)
    meta_file.write_text(json.dumps({"video": {"prompt": "hi", "model": "Realism"}}),
                         encoding="utf-8")
    config = make_config(tmp_path, regen_media_root=media_root, regen_metadata_root=metadata_root)
    providers = (WebProvider(marker="provider", gallery_url="https://example.com/image/{id}"),)

    with patch("fun_time.lock.mark_favorite"), \
         patch("fun_time.lock.load_web_providers", return_value=providers):
        _state, [opened] = a_lock_holds(str(video), BridgeState(), config)

    assert '"https://example.com/video#ft=' in _page_of(opened.key)
