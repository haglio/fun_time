from __future__ import annotations

import os
import re
import sys
import time

import pytest

from fun_time.win32 import window_title
from tests.integration.throwaway_chrome import WINDOW_TIMEOUT_S

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Chrome hears Windows' own display notices"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

MARKER = "FUNTIMEMARK-DISPLAY-OFF-PAGE"
FRAMES_PER_TITLE = 10
A_SECOND_OF_FRAMES = 60

FRAME_COUNTING_PAGE = f"""<script>
 let drawn = 0;
 requestAnimationFrame(function draw() {{
   drawn += 1;
   if (drawn % {FRAMES_PER_TITLE} === 0) document.title = "{MARKER} " + drawn;
   requestAnimationFrame(draw);
 }});
</script>"""


def _frames_drawn(page: int) -> int:
    counted = re.search(rf"{MARKER} (\d+)", window_title(page))
    return int(counted.group(1)) if counted else 0


def _frames_drawn_once_past(page: int, frames: int) -> int:
    deadline = time.monotonic() + WINDOW_TIMEOUT_S
    while (drawn := _frames_drawn(page)) <= frames and time.monotonic() < deadline:
        time.sleep(0.2)
    return drawn


def test_a_page_goes_on_painting_while_windows_says_the_display_is_off(chrome):
    page = chrome.open_page(MARKER, FRAME_COUNTING_PAGE)
    drawn = _frames_drawn_once_past(page, 0)
    assert drawn

    chrome.hear_that_the_display_is_off()

    assert _frames_drawn_once_past(page, drawn + A_SECOND_OF_FRAMES) > drawn + A_SECOND_OF_FRAMES
