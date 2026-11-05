from __future__ import annotations

from unittest.mock import patch

from app_support import favorites

from fun_time.content import WebProvider, load_web_providers
from fun_time.lock import a_hosted_lock_holds, a_lock_holds, build_discard_plan, go_to_line
from fun_time.media_metadata import metadata_path_for, recorded_for
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
    assert plan.message_text == "Unfavorited"


def test_discarding_a_non_favorite_condemns_it():
    plan = build_discard_plan(funestra=2, current_path="C:/v/odd.mp4", is_favorite=False)

    assert plan.condemns is True
    assert plan.message_text == "Sent to Trash"


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
