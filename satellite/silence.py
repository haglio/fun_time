from __future__ import annotations

import os

from fun_time.win32_desktop import on_hidden_desktop


def silenced() -> bool:
    return os.environ.get("FUN_TIME_MUTE_AUDIO") == "1" or on_hidden_desktop()
