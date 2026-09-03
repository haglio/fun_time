from __future__ import annotations

from unittest.mock import patch

import pytest

from fun_time.monitors import (
    MonitorInfo,
    get_logical_monitor_rects,
)
from fun_time.window_layout import MonitorRect


def _mi(x: int, y: int, w: int, h: int) -> MonitorInfo:
    return MonitorInfo(x=x, y=y, width=w, height=h)


class TestOrientationCorrection:
    """Landscape beats portrait for the main role; ties go to the leftmost."""

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


class TestTheVirtualDesktop:
    """The box every monitor sits inside — what a cover is sized by.

    Read with four bare indices into GetSystemMetrics inside a tkinter
    constructor before this; nothing named them and nothing tested them.
    """

    def test_the_four_metrics_come_back_as_one_rect(self):
        import ctypes

        from fun_time.monitors import (
            SM_CXVIRTUALSCREEN,
            SM_CYVIRTUALSCREEN,
            SM_XVIRTUALSCREEN,
            SM_YVIRTUALSCREEN,
            virtual_desktop_rect,
        )

        answers = {SM_XVIRTUALSCREEN: -1920, SM_YVIRTUALSCREEN: -100,
                   SM_CXVIRTUALSCREEN: 3840, SM_CYVIRTUALSCREEN: 1180}
        with patch.object(ctypes.windll.user32, "GetSystemMetrics", answers.get), \
             patch.object(ctypes.windll.user32, "SetProcessDPIAware", lambda: 1):
            rect = virtual_desktop_rect()

        assert (rect.x, rect.y, rect.width, rect.height) == (-1920, -100, 3840, 1180)

    def test_a_desktop_whose_size_cannot_be_read_is_no_rect(self):
        """Off Windows there is no ``windll`` to ask, and the caller has its own
        answer to fall back on — so this says so rather than raising into a
        constructor that has a window half-built."""
        import ctypes

        from fun_time.monitors import virtual_desktop_rect

        with patch.object(ctypes.windll.user32, "SetProcessDPIAware",
                          side_effect=AttributeError("no windll")):
            assert virtual_desktop_rect() is None

    def test_the_dpi_awareness_is_claimed_before_the_metrics_are_asked_for(self):
        """Unaware, Windows answers with the scaled numbers and the cover comes
        up short of the real desktop on a display that is not at 100%."""
        import ctypes

        from fun_time.monitors import virtual_desktop_rect

        order: list[str] = []
        with patch.object(ctypes.windll.user32, "SetProcessDPIAware",
                          side_effect=lambda: order.append("dpi")), \
             patch.object(ctypes.windll.user32, "GetSystemMetrics",
                          side_effect=lambda _i: (order.append("metric"), 0)[1]):
            virtual_desktop_rect()

        assert order[0] == "dpi"
