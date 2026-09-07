"""The cover Fun Time VR hangs in the headset while the session is changing.

A headset has no monitors for the desktop's overlay windows to sit on, so this
cover is a surface the VR player draws over its own scene, on the desktop's
channel (:mod:`fun_time.overlay_progress`) -- only the phase lists differ.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from fun_time.cover_palette import (
    BG,
    HINT_DIM,
    TEXT_DIM,
    TROUGH,
    WORDMARK_MAGENTA,
)
from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    Phase,
    parse_progress,
    ready_file_for,
)
from fun_time.project_paths import PROJECT_VR_ICON
from fun_time.session_handoff import headset_hold_asked

logger = logging.getLogger(__name__)

# Shorter than the desktop's: no browser, no Origenerator, no windows.
VR_STARTUP_PHASES: tuple[Phase, ...] = (
    Phase("services", "Preparing services...", 0.7),
    Phase("companions", "Launching companions...", 0.6),
    Phase("players", "Waiting for players...", 9.0),
    Phase("finalizing", "Finalizing...", 0.0),  # weightless: the bar reads full
)

# The player's phase is last because it is killed last: it wears the cover.
VR_SHUTDOWN_PHASES: tuple[Phase, ...] = (
    Phase("controls", "Closing...", 1.0),  # the cover opens on this wording
    Phase("companions", "Closing companions...", 1.0),
    Phase("players", "Closing players...", 1.0),
)

# What each waits on, and why they differ: docs/entering-vr.md.
STARTUP_STALE_TIMEOUT_S = 120.0
SHUTDOWN_STALE_TIMEOUT_S = 20.0

CANCEL_HINT = "Press Esc to cancel"  # through the hook, which needs no focus
CANCELLING_STATUS = "Cancelling..."
CLOSING_STATUS = VR_SHUTDOWN_PHASES[0].message
HELD_STATUS = "Returning to Fun Time..."  # exempt from staleness: see the doc

_STARTUP = "startup"
_SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class Cover:  # what the cover says now; *closing* is teardown's
    status: str
    fraction: float
    hint: str = ""
    closing: bool = False


class CoverWatcher:
    """What the cover should show, read off the orchestrator's progress files
    and polled from the player's worker, never its frame loop.  None means show
    the scene: no end running, DONE, or a file gone stale."""

    def __init__(
        self, state_dir: str | Path, *, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        state_dir = Path(state_dir)
        self.state_dir = state_dir
        self.startup_file = state_dir / PROGRESS_FILENAME
        self.shutdown_file = state_dir / SHUTDOWN_PROGRESS_FILENAME
        self.cancel_file = state_dir / CANCEL_FILENAME
        self.ready_file = ready_file_for(self.shutdown_file)
        self._clock = clock
        self._closing_locally = False
        self._cancelling = False
        self._held: dict[str, tuple[float, str]] = {}
        self._seen: dict[str, tuple[str, float]] = {}
        self._gave_up: set[str] = set()

    def closing_now(self) -> None:
        """Raise the closing cover with no orchestrator to ask for it."""
        self._closing_locally = True

    def read(self) -> Cover | None:
        """This tick's cover, or None to show the scene."""
        if headset_hold_asked(self.state_dir):
            return Cover(status=HELD_STATUS, fraction=1.0, closing=True)
        # Teardown outranks startup, whatever its file still says.
        shutdown = self._read_end(
            self.shutdown_file, key=_SHUTDOWN,
            stale_timeout_s=SHUTDOWN_STALE_TIMEOUT_S, opening=CLOSING_STATUS,
        )
        if shutdown is not None:
            return Cover(status=shutdown[1], fraction=shutdown[0], closing=True)
        if self._closing_locally:
            return Cover(status=CLOSING_STATUS, fraction=0.0, closing=True)
        startup = self._read_end(
            self.startup_file, key=_STARTUP,
            stale_timeout_s=STARTUP_STALE_TIMEOUT_S, opening=VR_STARTUP_PHASES[0].message,
        )
        if startup is None:
            return None
        fraction, message = startup
        if self._cancel_asked():  # and no way out left to offer
            return Cover(status=CANCELLING_STATUS, fraction=fraction)
        return Cover(status=message, fraction=fraction, hint=CANCEL_HINT)

    def _read_end(
        self, path: Path, *, key: str, stale_timeout_s: float, opening: str
    ) -> tuple[float, str] | None:
        """``(fraction, message)``, or None when that end is not running."""
        if key in self._gave_up:
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None  # no file: that end of the session is not running
        progress = parse_progress(text)
        if progress.done:
            return None
        if self._went_stale(key, text, stale_timeout_s):
            logger.warning("%s progress has not moved in %.0fs; taking the cover down",
                           key, stale_timeout_s)
            self._gave_up.add(key)
            return None
        held = self._held.get(key)
        if progress.malformed:  # a torn write is not a step
            return held if held is not None else (0.0, opening)
        fraction = progress.step / progress.total if progress.total > 0 else 0.0
        message = progress.message or (held[1] if held is not None else opening)
        self._held[key] = (fraction, message)
        return fraction, message

    def _cancel_asked(self) -> bool:
        """Latched: the flag is dropped at the END of the teardown it starts."""
        if self._cancelling:
            return True
        try:
            self._cancelling = self.cancel_file.exists()
        except OSError:
            return False
        return self._cancelling

    def _went_stale(self, key: str, text: str, stale_timeout_s: float) -> bool:
        """Whether the file has said the same thing for too long -- judged on
        what it SAYS, since two steps written inside one filesystem timestamp
        tick share an mtime, and a clock keyed on that never restarts."""
        now = self._clock()
        seen = self._seen.get(key)
        if seen is None or seen[0] != text:
            self._seen[key] = (text, now)
            return False
        return now - seen[1] > stale_timeout_s


# The player's answer to "is the room on screen?", which a desktop orchestrator
# sees for itself.  The grace caps it -- an empty satellite playlist never gets
# a texture -- and the dwell is the cover's time in front of a WORN headset
# before the room may be revealed (docs/entering-vr.md).
SCENE_READY_FILENAME = "vr_scene_ready.flag"
SCENE_READY_GRACE_S = 25.0
COVER_DWELL_S = 2.0


class CoverSeen:
    """Whether the cover has been in front of the viewer long enough to read.
    Fed the frames that reached a WORN headset, not the ones submitted: with the
    views unlocatable or the headset on the desk, nothing is shown."""

    def __init__(
        self,
        *,
        dwell_s: float = COVER_DWELL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dwell_s = dwell_s
        self._clock = clock
        self._first: float | None = None

    def note(self, shown: bool) -> None:  # starts the clock on the first one
        if shown and self._first is None:
            self._first = self._clock()

    @property
    def dwelt(self) -> bool:
        return self._first is not None and self._clock() - self._first >= self._dwell_s


def scene_ready_file(state_dir: str | Path) -> Path:
    return Path(state_dir) / SCENE_READY_FILENAME


class SceneReady:
    """Watches the room fill in under the cover, and says once when it has.
    Without it the reveal lands on the player's first STATUS write: a role
    having PICKED a video, a second before the pictures land."""

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

    def note(self, ready: bool) -> None:
        """One frame's answer, written once: on the first yes, or on the grace
        running out."""
        if self._reported:
            return
        now = self._clock()
        if self._started is None:
            self._started = now
        if not ready and now - self._started < self._grace_s:
            return
        if not ready:
            logger.warning("Some of the room is still blank after %.0fs; revealing anyway",
                           self._grace_s)
        self._reported = True
        try:
            self._file.write_text("", encoding="utf-8")
        except OSError:
            logger.warning("Could not report the room ready", exc_info=True)


def wait_for_cover_painted(
    ready_file: Path,
    *,
    still_alive: Callable[[], bool],
    timeout_s: float,
    poll_s: float = 0.05,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Block until the player reports the closing cover painted in the headset.
    Two ways out besides the flag, both the desktop's: the player is gone, so no
    flag is coming; or waiting costs more than the flicker would."""
    deadline = clock() + timeout_s
    while clock() < deadline:
        if ready_file.exists():
            return True
        if not still_alive():
            logger.warning("The VR player was gone before it could raise the closing cover")
            return False
        sleep(poll_s)
    logger.warning("No closing cover after %.1fs; closing anyway", timeout_s)
    return False


# --- Painting -------------------------------------------------------------

COVER_SIZE_PX = (512, 320)  # held: a changed size rescales the whole cover

COVER_WIDTH_DEG = 40.0  # wider than the console's 24: that is glanced at


class CoverAnchor:
    """Where the cover hangs: the heading the viewer had when it went up, held
    until it comes down.  Head-locked it turns with the eyes and reads as glued
    to the lenses; held to one heading it is out in the world.  Captured, not
    fixed at the scene's forward, so it arrives in front of whoever raised
    it."""

    def __init__(self) -> None:
        self._yaw: float | None = None

    def heading(self, yaw: float) -> float:  # takes *yaw* only if none is held
        if self._yaw is None:
            self._yaw = yaw
        return self._yaw

    def release(self) -> None:
        self._yaw = None

# Segoe UI as filenames: Pillow loads a face by file, not by family.
_WORDMARK_FONT = "segoeuiz.ttf"
_BODY_FONT = "segoeui.ttf"

_ICON_PX = 96
_WORDMARK = "Fun Time VR"
_WORDMARK_PT = 30
_STATUS_PT = 17
_HINT_PT = 13
_BAR = (360, 18)  # the desktop bar's length and thickness
_GAPS = (14, 12, 14, 12)  # icon->wordmark, wordmark->status, status->bar, bar->hint


def _clear_color(hex_color: str) -> tuple[float, float, float, float]:
    """A palette tone as ``glClearColor``'s floats, straight through: nothing
    enables GL_FRAMEBUFFER_SRGB, so decoding would light it."""
    value = hex_color.lstrip("#")
    return (*(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4)), 1.0)


COVER_CLEAR = _clear_color(BG)


def _font(filename: str, size: int) -> ImageFont.FreeTypeFont:  # never raises
    try:
        return ImageFont.truetype(filename, size)
    except OSError:
        return ImageFont.load_default(size)


def _icon_image() -> Image.Image | None:
    try:
        icon = Image.open(PROJECT_VR_ICON)
        return icon.resize((_ICON_PX, _ICON_PX), Image.LANCZOS).convert("RGBA")
    except (OSError, ValueError):
        return None  # not there, or not an image: plain


def _text_height(font: ImageFont.FreeTypeFont, text: str) -> int:
    top, base = font.getbbox(text)[1], font.getbbox(text)[3]
    return max(1, base - top)


def paint_cover(cover: Cover, *, size: tuple[int, int] = COVER_SIZE_PX) -> Image.Image:
    """The desktop cover's panel, in its five tones, on a filled ground."""
    width, height = size
    image = Image.new("RGBA", size, BG)
    draw = ImageDraw.Draw(image)
    wordmark_font = _font(_WORDMARK_FONT, _WORDMARK_PT)
    status_font = _font(_BODY_FONT, _STATUS_PT)
    hint_font = _font(_BODY_FONT, _HINT_PT)

    icon = _icon_image()
    rows = [
        _ICON_PX if icon is not None else 0,
        _text_height(wordmark_font, _WORDMARK),
        _text_height(status_font, cover.status or " "),
        _BAR[1],
        _text_height(hint_font, cover.hint or " "),
    ]
    y = (height - (sum(rows) + sum(_GAPS))) // 2
    center = width // 2

    if icon is not None:
        image.alpha_composite(icon, (center - _ICON_PX // 2, y))
        y += rows[0] + _GAPS[0]
    _centered(draw, center, y, _WORDMARK, wordmark_font, WORDMARK_MAGENTA)
    y += rows[1] + _GAPS[1]
    _centered(draw, center, y, cover.status, status_font, TEXT_DIM)
    y += rows[2] + _GAPS[2]
    _bar(draw, center, y, cover.fraction)
    y += rows[3] + _GAPS[3]
    if cover.hint:
        _centered(draw, center, y, cover.hint, hint_font, HINT_DIM)
    return image


def _centered(draw, center_x: int, top: int, text: str, font, fill: str) -> None:
    if not text:
        return
    # Ink-top anchored, so measured and drawn agree.
    draw.text((center_x - font.getlength(text) / 2, top - font.getbbox(text)[1]),
              text, font=font, fill=fill)


def _bar(draw, center_x: int, top: int, fraction: float) -> None:
    bar_width, bar_height = _BAR
    left = center_x - bar_width // 2
    draw.rectangle([left, top, left + bar_width, top + bar_height], fill=TROUGH)
    filled = round(bar_width * max(0.0, min(1.0, fraction)))
    if filled:
        draw.rectangle([left, top, left + filled, top + bar_height], fill=WORDMARK_MAGENTA)
