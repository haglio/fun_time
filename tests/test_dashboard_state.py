from __future__ import annotations

from fun_time.dashboard_actions import DASHBOARD_ACTION_IDS, LINK_TOGGLE, QUARTER_BUTTON
from fun_time.dashboard_state import (
    LABEL_PRIMARY_ROBOT,
    LABEL_PRIMARY_VLC,
    LABEL_PORTRAIT_VLC,
    panel_label_text,
    primary_panel_should_highlight,
    satellite_panel_should_highlight,
)


def test_panel_label_text_matches_dashboard_multiline_labels():
    assert panel_label_text(LABEL_PORTRAIT_VLC) == "Portrait AI\nVLC"
    assert panel_label_text(LABEL_PRIMARY_VLC) == "Non-AI\nVLC"
    assert panel_label_text(LABEL_PRIMARY_ROBOT) == "Non-AI\nRobot Hand"


def test_primary_panel_highlight_follows_f_mode_or_funscript():
    assert primary_panel_should_highlight(
        f_mode_enabled=True,
        primary_path="",
        has_matching_funscript=False,
    )
    assert primary_panel_should_highlight(
        f_mode_enabled=False,
        primary_path="clip.mp4",
        has_matching_funscript=True,
    )
    assert not primary_panel_should_highlight(
        f_mode_enabled=False,
        primary_path="clip.mp4",
        has_matching_funscript=False,
    )


def test_satellite_panel_highlight_follows_f_mode_or_favorite():
    assert satellite_panel_should_highlight(f_mode_enabled=True, is_favorite=False)
    assert satellite_panel_should_highlight(f_mode_enabled=False, is_favorite=True)
    assert not satellite_panel_should_highlight(f_mode_enabled=False, is_favorite=False)


def test_dashboard_action_vocabulary_covers_current_click_targets():
    assert LINK_TOGGLE in DASHBOARD_ACTION_IDS
    assert QUARTER_BUTTON in DASHBOARD_ACTION_IDS
    assert len(DASHBOARD_ACTION_IDS) == len(set(DASHBOARD_ACTION_IDS))
