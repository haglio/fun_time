from __future__ import annotations

from fun_time.lock import a_lock_holds, build_discard_plan
from fun_time.watch_stats import watch_stats_path
from tests.test_windows_bridge_dispatch_loop import make_config


def test_a_lock_with_nothing_on_screen_holds_nothing(tmp_path):
    config = make_config(tmp_path)

    assert a_lock_holds("", config) == []
    assert not config.favs_file.exists()
    assert not watch_stats_path(config.state_dir).exists()


def test_discarding_a_favorite_is_a_demotion_not_a_condemnation():
    plan = build_discard_plan(funestra=3, current_path="C:/v/kept.mp4", is_favorite=True)

    assert plan.condemns is False
    assert plan.notice_message == "Unfavorited"


def test_discarding_a_non_favorite_condemns_it():
    plan = build_discard_plan(funestra=2, current_path="C:/v/odd.mp4", is_favorite=False)

    assert plan.condemns is True
    assert plan.notice_message == "Marked weird"
