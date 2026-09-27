from __future__ import annotations

import pytest

from fun_time.chrome_accessibility import open_chrome_window

A_WINDOW_THAT_COULD_BE_HIS_CHROME = 7777


def test_no_unit_test_reaches_a_real_browsers_tabs():
    with pytest.raises(RuntimeError, match="real browser"):
        open_chrome_window(A_WINDOW_THAT_COULD_BE_HIS_CHROME)
