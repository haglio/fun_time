"""The hosted Origenerator's own window, on a screen in the room."""
from __future__ import annotations

from pathlib import Path

from player_core.file_channel import append_command

from .frame_channel import FrameReader
from .pointer import RELEASE, PressEvent, surface_pixel
from .thumbs import CONTROLLER_DEADZONE

FRAME_FILENAME = "origenerator_frame.bin"
INPUT_FILENAME = "origenerator_input.txt"

# The words this module says by itself; a press, drag or right-click travels as
# the event's own kind, so pointer's names for those are the app's words too.
# The launch test holds all of them to the app's contract file.
HOSTED_RELEASE = "release"
HOVER = "hover"
SCROLL = "scroll"

WHEEL_NOTCH = 120  # one notch, in the eighths of a degree Qt counts them in
_NOTCHES_PER_S = 8.0

TOKEN = 0  # this window is never re-opened, so it needs no token of its own


def event_line(event: PressEvent, size: tuple[int, int]) -> str:
    if event.kind == RELEASE:
        return HOSTED_RELEASE
    x, y = surface_pixel(event.u, event.v, size)
    return f"{event.kind} {x} {y}"


def hover_line(x: int, y: int) -> str:
    return f"{HOVER} {x} {y}"


def scroll_line(delta: int) -> str:
    return f"{SCROLL} {delta}"


def scroll_from_stick(axis: float, elapsed_s: float) -> float:
    if abs(axis) <= CONTROLLER_DEADZONE:
        return 0.0
    return axis * elapsed_s * _NOTCHES_PER_S * WHEEL_NOTCH


class GalleryPanel:
    """The channel pair it hands the window over through."""

    def __init__(self, state_dir: Path) -> None:
        self._input = Path(state_dir) / INPUT_FILENAME
        self._frames = FrameReader(Path(state_dir) / FRAME_FILENAME)
        self._size: tuple[int, int] | None = None

    @property
    def size(self) -> tuple[int, int] | None:
        return self._size

    def frame(self) -> tuple[int, int, bytes] | None:
        picture = self._frames.latest(TOKEN)
        if picture is not None:
            self._size = (picture[0], picture[1])
        return picture

    def send(self, line: str) -> None:
        append_command(self._input, line)

    def press(self, event: PressEvent) -> None:
        # Dropped where no picture has arrived to place it on.
        if self._size is None:
            return
        self.send(event_line(event, self._size))

    def close(self) -> None:
        self._frames.close()
