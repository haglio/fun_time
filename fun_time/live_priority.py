from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable

from app_support.win32 import put_this_process_at

logger = logging.getLogger(__name__)

AHEAD_OF_BACKGROUND_WORK = subprocess.ABOVE_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 0


def put_this_session_ahead_of_background_work(
        put: Callable[[int], int | None] = put_this_process_at) -> None:
    if put(AHEAD_OF_BACKGROUND_WORK) is None:
        logger.warning("Windows would not put Fun Time ahead of background work, so a busy "
                       "computer can make keys, spoken commands and the Dashboard answer late")
