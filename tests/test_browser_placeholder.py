from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from fun_time import browser_placeholder
from fun_time.browser_placeholder import stand_unseen_over
from fun_time.window_layout import WindowRect
from tests.integration.integration_support import environment_with_this_checkouts_siblings

RECT = WindowRect(x=10, y=20, width=300, height=400)


def _stood(*, found: int = 777, **kwargs):
    start = MagicMock(return_value=MagicMock(pid=5))
    with patch.object(browser_placeholder, "tie_to_this_process"), \
            patch.object(browser_placeholder, "wait_for_window_by_title",
                         side_effect=lambda _title, **_kw: found):
        placeholder = stand_unseen_over(RECT, sibling_checkouts="", start=start, **kwargs)
    return placeholder, start


def test_the_placeholder_is_a_process_of_its_own_over_the_browsers_place():
    placeholder, start = _stood()

    argv = start.call_args.args[0]
    assert argv[1:] == ["-m", "fun_time.browser_placeholder", "10", "20", "300", "400"]
    assert placeholder.hwnd == 777


def test_taking_it_down_ends_its_process():
    placeholder, _start = _stood()
    placeholder.process.poll.return_value = None

    placeholder.take_it_down()

    placeholder.process.kill.assert_called_once()


def test_a_placeholder_that_never_comes_up_is_said_and_the_browser_opens_without_it(caplog):
    with caplog.at_level(logging.WARNING, logger="fun_time.browser_placeholder"):
        placeholder, _start = _stood(found=0)

    assert placeholder.hwnd == 0
    assert "was not up within" in caplog.text


def test_standing_the_placeholder_loads_no_tk_into_the_process_that_stands_it():
    loaded = subprocess.run(
        [sys.executable, "-c",
         "import sys\n"
         "from unittest.mock import MagicMock\n"
         "from fun_time import browser_placeholder as b\n"
         "from fun_time.window_layout import WindowRect\n"
         "b.tie_to_this_process = lambda _p: None\n"
         "b.wait_for_window_by_title = lambda _t, **_kw: 0\n"
         "b.stand_unseen_over(WindowRect(0, 0, 1, 1), sibling_checkouts='', start=MagicMock())\n"
         "print('tkinter' in sys.modules)\n"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        env=environment_with_this_checkouts_siblings(), check=False,
    )

    assert loaded.stdout.strip().splitlines()[-1:] == ["False"], loaded.stdout + loaded.stderr
