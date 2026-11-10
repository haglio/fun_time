from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from shared_ui.loading_panel import LoadingPanel, Metrics, icon_image, render
from shared_ui.palette import LOADING_GROUND
from shared_ui.spacing import GAP_DIALOG
from shared_ui.type_scale import SIZE_BODY, SIZE_HEADING, SIZE_SMALL, in_pixels

from fun_time import preview_marker
from fun_time.overlay_progress import (
    ACTIVATING_ORIGENERATOR,
    CANCEL_FILENAME,
    CANCEL_WORD,
    CANCELING,
    FINALIZING_UNDER_A_FULL_BAR,
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    STARTING_VOICE_COMMANDS,
    Phase,
    Progress,
    parse_progress,
    ready_file_for,
    what_the_flag_asks,
)
from fun_time.project_paths import PROJECT_VR_ICON
from fun_time.session_handoff import DESKTOP, crossing_progress_path, headset_hold_asked

logger = logging.getLogger(__name__)

VR_STARTUP_PHASES: tuple[Phase, ...] = (
    Phase("services", "Preparing services...", 0.7),
    Phase("companions", "Launching companions...", 0.6),
    Phase("funestras", "Waiting for Funestras...", 9.0),
    ACTIVATING_ORIGENERATOR,
    STARTING_VOICE_COMMANDS,
    FINALIZING_UNDER_A_FULL_BAR,
)

VR_SHUTDOWN_PHASES: tuple[Phase, ...] = (
    Phase("controls", "Closing...", 1.0),
    Phase("companions", "Closing companions...", 1.0),
    Phase("funestras", "Closing Funestras...", 1.0),
)

STARTUP_STALE_TIMEOUT_S = 120.0
SHUTDOWN_STALE_TIMEOUT_S = 20.0

CLOSING_STATUS = VR_SHUTDOWN_PHASES[0].message
HELD_STATUS = DESKTOP.crossing_message
WEARER_STATUS = "Waiting for you to put the headset on..."

_STARTUP = "startup"
_SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class LoadingScreen:
    status: str
    fraction: float
    hint: str = ""
    closing: bool = False


def _line_on(path: Path) -> Progress | None:
    try:
        return parse_progress(path.read_text(encoding="utf-8"))
    except OSError:
        return None


class LoadingScreenWatcher:
    def __init__(
        self, state_dir: str | Path, *, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        state_dir = Path(state_dir)
        self.state_dir = state_dir
        self.startup_file = state_dir / PROGRESS_FILENAME
        self.shutdown_file = state_dir / SHUTDOWN_PROGRESS_FILENAME
        self.cancel_file = state_dir / CANCEL_FILENAME
        self.crossing_file = crossing_progress_path(state_dir)
        self.ready_file = ready_file_for(self.shutdown_file)
        self._clock = clock
        self._closing_locally = False
        self._cancelling = False
        self._held: dict[str, tuple[float, str]] = {}
        self._seen: dict[str, tuple[tuple[str, float], float]] = {}
        self._gave_up: set[str] = set()

    def closing_unasked(self) -> None:
        self._closing_locally = True

    def read(self) -> LoadingScreen | None:
        if headset_hold_asked(self.state_dir):
            return self._screen_for_the_line(1.0, *self._held_line(), closing=True)
        shutdown = self._read_end(
            self.shutdown_file, key=_SHUTDOWN,
            stale_timeout_s=SHUTDOWN_STALE_TIMEOUT_S, opening=CLOSING_STATUS,
        )
        if shutdown is not None:
            return self._screen_for_the_line(*shutdown, closing=True)
        if self._closing_locally:
            return LoadingScreen(status=CLOSING_STATUS, fraction=0.0, closing=True)
        startup = self._read_end(
            self.startup_file, key=_STARTUP,
            stale_timeout_s=STARTUP_STALE_TIMEOUT_S, opening=VR_STARTUP_PHASES[0].message,
        )
        return None if startup is None else self._screen_for_the_line(*startup, closing=False)

    def _screen_for_the_line(
        self, fraction: float, message: str, hint: str, *, closing: bool,
    ) -> LoadingScreen:
        if self._cancelling or (hint and self._cancel_asked()):
            return LoadingScreen(status=CANCELING, fraction=fraction, closing=closing)
        return LoadingScreen(status=message, fraction=fraction, hint=hint, closing=closing)

    def _held_line(self) -> tuple[str, str]:
        crossing = _line_on(self.crossing_file)
        if crossing is not None and crossing.message:
            return crossing.message, crossing.hint
        startup = _line_on(self.startup_file)
        return HELD_STATUS, startup.hint if startup is not None else ""

    def _read_end(
        self, path: Path, *, key: str, stale_timeout_s: float, opening: str
    ) -> tuple[float, str, str] | None:
        if key in self._gave_up:
            return None
        try:
            text = path.read_text(encoding="utf-8")
            moved_at = path.stat().st_mtime
        except OSError:
            return None
        progress = parse_progress(text)
        if progress.done:
            return None
        if self._went_stale(key, (text, moved_at), stale_timeout_s):
            logger.warning("%s progress has not moved in %.0fs; taking the loading screen down",
                           key, stale_timeout_s)
            self._gave_up.add(key)
            return None
        held = self._held.get(key)
        if progress.malformed:
            return held if held is not None else (0.0, opening, "")
        fraction = progress.step / progress.total if progress.total > 0 else 0.0
        message = progress.message or (held[1] if held is not None else opening)
        self._held[key] = (fraction, message, progress.hint)
        return self._held[key]

    def _cancel_asked(self) -> bool:
        if not self._cancelling:
            self._cancelling = what_the_flag_asks(self.cancel_file) == CANCEL_WORD
        return self._cancelling

    def _went_stale(self, key: str, said: tuple[str, float], stale_timeout_s: float) -> bool:
        now = self._clock()
        seen = self._seen.get(key)
        if seen is None or seen[0] != said:
            self._seen[key] = (said, now)
            return False
        return now - seen[1] > stale_timeout_s


SCENE_READY_FILENAME = "vr_scene_ready.flag"
SCENE_READY_GRACE_S = 25.0
LOADING_SCREEN_DWELL_S = 2.0


class LoadingScreenDwell:
    def __init__(
        self,
        *,
        dwell_s: float = LOADING_SCREEN_DWELL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dwell_s = dwell_s
        self._clock = clock
        self._first: float | None = None

    def note(self, shown: bool) -> None:
        if shown:
            if self._first is None:
                self._first = self._clock()
        elif not self.dwelt:
            self._first = None

    @property
    def dwelt(self) -> bool:
        return self._first is not None and self._clock() - self._first >= self._dwell_s


def scene_ready_file(state_dir: str | Path) -> Path:
    return Path(state_dir) / SCENE_READY_FILENAME


class SceneReady:
    def __init__(
        self,
        marker_file: Path,
        *,
        grace_s: float = SCENE_READY_GRACE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._file = marker_file
        self._grace_s = grace_s
        self._clock = clock
        self._started: float | None = None
        self._reported = False

    def note(self, *, blank: Sequence[str], read: bool) -> None:
        if self._reported:
            return
        now = self._clock()
        if self._started is None:
            self._started = now
        waited = now - self._started
        if not blank and read:
            logger.info("Room up and the loading screen read after %.1fs", waited)
        elif waited < self._grace_s:
            return
        elif blank:
            logger.warning("No frame yet on %s after %.0fs; revealing anyway",
                           ", ".join(blank), self._grace_s)
        else:
            logger.warning("The loading screen never had %.0fs on screen; revealing anyway",
                           self._grace_s)
        self._reported = True
        try:
            self._file.write_text("", encoding="utf-8")
        except OSError:
            logger.warning("Could not report the room ready", exc_info=True)


_CLOSING_SCREEN_POLL_S = 0.05


def wait_for_loading_screen_painted(
    ready_file: Path,
    *,
    still_alive: Callable[[], bool],
    timeout_s: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = clock() + timeout_s
    while clock() < deadline:
        if ready_file.exists():
            return True
        if not still_alive():
            logger.warning("The headset was gone before it could raise the closing screen")
            return False
        sleep(_CLOSING_SCREEN_POLL_S)
    logger.warning("No closing screen after %.1fs; closing anyway", timeout_s)
    return False


LOADING_SCREEN_SIZE_PX = (512, 320)

LOADING_SCREEN_WIDTH_DEG = 40.0


class LoadingScreenAnchor:
    def __init__(self) -> None:
        self._yaw: float | None = None

    def heading(self, yaw: float, *, settled: bool = True) -> float:
        if not settled:
            return yaw
        if self._yaw is None:
            self._yaw = yaw
        return self._yaw

    @property
    def held(self) -> float | None:
        return self._yaw

    def release(self) -> None:
        self._yaw = None

_WORDMARK = "Fun Time VR"

HEADSET = Metrics(icon=96, wordmark_px=in_pixels(SIZE_HEADING), status_px=in_pixels(SIZE_BODY),
                  hint_px=in_pixels(SIZE_SMALL), gaps=(GAP_DIALOG,) * 4)

LOADING_SCREEN_CLEAR = (*(channel / 255.0 for channel in LOADING_GROUND), 1.0)


def paint_loading_screen(loading_screen: LoadingScreen, *, size: tuple[int, int] = LOADING_SCREEN_SIZE_PX) -> Image.Image:
    panel = render(LoadingPanel(
        wordmark=_WORDMARK,
        status=loading_screen.status,
        fraction=loading_screen.fraction,
        hint=loading_screen.hint,
        ink=preview_marker.wordmark_ink(preview_marker.shown_as()),
        icon=icon_image(preview_marker.icon_file(PROJECT_VR_ICON, preview_marker.shown_as())),
    ), HEADSET)
    image = Image.new("RGBA", size, (*LOADING_GROUND, 255))
    image.paste(panel, ((size[0] - panel.width) // 2, (size[1] - panel.height) // 2))
    return image
