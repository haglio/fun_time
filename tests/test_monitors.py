from __future__ import annotations

import pytest

from fun_time.monitors import (
    MonitorInfo,
    get_logical_monitor_rects,
)
from fun_time.window_layout import MonitorRect


def _mi(x: int, y: int, w: int, h: int) -> MonitorInfo:
    return MonitorInfo(x=x, y=y, width=w, height=h)


class TestGetLogicalMonitorRects:
    """Replicate the AHK GetLogicalMonitorRects orientation-correction logic."""

    def test_landscape_main_portrait_secondary_keeps_assignment(self):
        monitors = [_mi(0, 0, 2560, 1392), _mi(2560, 0, 1440, 3440)]
        main, secondary = get_logical_monitor_rects(monitors, primary_index=1, secondary_index=2)

        assert main == MonitorRect(0, 0, 2560, 1392)
        assert secondary == MonitorRect(2560, 0, 1440, 3440)

    def test_swapped_config_corrects_to_landscape_main(self):
        monitors = [_mi(0, 0, 2560, 1392), _mi(2560, 0, 1440, 3440)]
        main, secondary = get_logical_monitor_rects(monitors, primary_index=2, secondary_index=1)

        assert main == MonitorRect(0, 0, 2560, 1392)
        assert secondary == MonitorRect(2560, 0, 1440, 3440)

    def test_both_landscape_falls_back_to_leftmost_main(self):
        monitors = [_mi(1920, 0, 1920, 1080), _mi(0, 0, 2560, 1440)]
        main, secondary = get_logical_monitor_rects(monitors, primary_index=1, secondary_index=2)

        assert main == MonitorRect(0, 0, 2560, 1440)
        assert secondary == MonitorRect(1920, 0, 1920, 1080)

    def test_both_portrait_falls_back_to_leftmost_main(self):
        monitors = [_mi(1440, 0, 1080, 1920), _mi(0, 0, 1440, 2560)]
        main, secondary = get_logical_monitor_rects(monitors, primary_index=1, secondary_index=2)

        assert main == MonitorRect(0, 0, 1440, 2560)
        assert secondary == MonitorRect(1440, 0, 1080, 1920)

    def test_single_monitor_uses_same_for_both(self):
        monitors = [_mi(0, 0, 2560, 1440)]
        main, secondary = get_logical_monitor_rects(monitors, primary_index=1, secondary_index=1)

        assert main == MonitorRect(0, 0, 2560, 1440)
        assert secondary == MonitorRect(0, 0, 2560, 1440)

    def test_index_clamped_to_available_monitors(self):
        monitors = [_mi(0, 0, 2560, 1440)]
        main, secondary = get_logical_monitor_rects(monitors, primary_index=1, secondary_index=5)

        assert main == MonitorRect(0, 0, 2560, 1440)
        assert secondary == MonitorRect(0, 0, 2560, 1440)

    def test_empty_monitors_raises(self):
        with pytest.raises(ValueError, match="No monitors"):
            get_logical_monitor_rects([], primary_index=1, secondary_index=2)
