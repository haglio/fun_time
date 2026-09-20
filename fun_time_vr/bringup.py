"""Opening a session on the headset, and what to say when it cannot be opened.
Out of :mod:`fun_time_vr.player`, which needs glfw, OpenGL and xr to import."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# How long bring-up tolerates a cold runtime whose graphics device is still
# coming up, and how often it retries; inside the orchestrator's 120s.
SESSION_BRINGUP_TIMEOUT_S = 60.0
SESSION_BRINGUP_RETRY_S = 2.0

_NOT_READY = (
    "Could not start a VR session.\n\nThe VR runtime started, but its graphics "
    "device never became ready.\n\nError: {error}"
)
_NO_SESSION = (
    "Could not start a VR session.\n\nThe headset answered, but FunTimeVR could "
    "not open a session on it.\n\nError: {error}"
)


@dataclass(frozen=True)
class Bringup:
    session: Any = None
    message: str = ""


def open_vr_session(
    make_session: Callable[[], Any],
    *,
    device_not_ready: type[BaseException],
    timeout_s: float = SESSION_BRINGUP_TIMEOUT_S,
    retry_s: float = SESSION_BRINGUP_RETRY_S,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Bringup:
    """Keep asking while the runtime is merely still warming up."""
    deadline = now() + timeout_s
    while True:
        try:
            return Bringup(session=make_session())
        except device_not_ready as exc:
            if now() >= deadline:
                logger.error("VR session bring-up failed: %s", exc)
                return Bringup(message=_NOT_READY.format(error=exc))
            logger.info("VR runtime's graphics device not ready yet; retrying bring-up")
            sleep(retry_s)
        except Exception as exc:
            logger.exception("VR session bring-up failed")
            return Bringup(message=_NO_SESSION.format(error=exc))
