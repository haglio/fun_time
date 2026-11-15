from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

STEP_LINE = "Startup step: %s took %.1fs"


@contextmanager
def timed_step(logger: logging.Logger, step: str, *,
               clock: Callable[[], float] = time.monotonic) -> Iterator[None]:
    began = clock()
    try:
        yield
    finally:
        logger.info(STEP_LINE, step, clock() - began)
