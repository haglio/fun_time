from __future__ import annotations

from unittest.mock import patch

from app_support import favorites

from fun_time.content import WebProvider
from fun_time.lock import a_lock_holds, build_discard_plan
from fun_time.media_metadata import recorded_for
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


def test_discarding_a_favorite_is_a_demotion_not_a_condemnation():
    plan = build_discard_plan(funestra=3, current_path="C:/v/kept.mp4", is_favorite=True)

    assert plan.condemns is False
    assert plan.notice_message == "Unfavorited"


def test_discarding_a_non_favorite_condemns_it():
    plan = build_discard_plan(funestra=2, current_path="C:/v/odd.mp4", is_favorite=False)

    assert plan.condemns is True
    assert plan.notice_message == "Marked weird"
