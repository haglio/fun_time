from __future__ import annotations

import pytest

from fun_time.chrome_accessibility import open_chrome_window

A_WINDOW_THAT_COULD_BE_HIS_CHROME = 7777


def test_no_unit_test_switches_the_tab_he_is_looking_at():
    with pytest.raises(RuntimeError, match="his own Chrome"):
        open_chrome_window(A_WINDOW_THAT_COULD_BE_HIS_CHROME)
