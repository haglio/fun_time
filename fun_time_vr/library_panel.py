from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs
from PIL import Image, ImageDraw
from player_core.file_channel import append_command, consume_command_file
from shared_ui.palette import BG_PRIMARY, TEXT_MUTED

from fun_time.process_identity import NAMER

from .frame_channel import FrameReader
from .lettering import load_font
from .pointer import RELEASE, PressEvent, surface_pixel
from .thumbs import CONTROLLER_DEADZONE

LIBRARY_SIZE_PX = (1280, 720)
HOST_MODULE = "fun_time_vr.library_host"
INPUT_FILENAME = "library_input.txt"
OUTPUT_FILENAME = "library_output.txt"
FRAME_FILENAME = "library_frame.bin"

OPEN = "open"
HOVER = "hover"
SCROLL = "scroll"
PICKED = "picked"
DISMISSED = "dismissed"

WHEEL_NOTCH = 120
_NOTCHES_PER_S = 8.0
_ENDING_S = 2.0
_READING = "Reading the library…"
_READING_PX = 20


def open_line(token: int, video: str) -> str:
    return f"{OPEN} {token} {video}".rstrip()


def event_line(event: PressEvent) -> str:
    if event.kind == RELEASE:
        return RELEASE
    x, y = surface_pixel(event.u, event.v, LIBRARY_SIZE_PX)
    return f"{event.kind} {x} {y}"


def hover_line(x: int, y: int) -> str:
    return f"{HOVER} {x} {y}"


def scroll_line(delta: int) -> str:
    return f"{SCROLL} {delta}"


def picked_line(video: str) -> str:
    return f"{PICKED} {video}"


def scroll_from_stick(axis: float, elapsed_s: float) -> float:
    if abs(axis) <= CONTROLLER_DEADZONE:
        return 0.0
    return axis * elapsed_s * _NOTCHES_PER_S * WHEEL_NOTCH


class ShownWhileAsked:
    def __init__(self) -> None:
        self.showing = False
        self._put_away = False

    def asked(self, up: bool) -> bool:
        if not up:
            self._put_away = False
        opening = up and not self._put_away and not self.showing
        self.showing = up and not self._put_away
        return opening

    def put_away(self) -> None:
        self._put_away = True
        self.showing = False


class LibraryHost:
    def __init__(
        self, *, manifest_path: Path, state_dir: Path, python_exe: str = sys.executable,
        launch: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self._input = Path(state_dir) / INPUT_FILENAME
        self._output = Path(state_dir) / OUTPUT_FILENAME
        frames = Path(state_dir) / FRAME_FILENAME
        for left_over in (self._input, self._output, frames):
            try:
                left_over.unlink(missing_ok=True)
            except OSError:
                pass
        self._frames = FrameReader(frames)
        self._process = launch(
            [NAMER.named_exe(python_exe, "LibraryBrowser"), "-m", HOST_MODULE, str(manifest_path),
             "--input", str(self._input), "--output", str(self._output),
             "--frames", str(frames), "--parent", str(os.getpid())],
            **hidden_subprocess_kwargs(),
        )

    def send(self, line: str) -> None:
        append_command(self._input, line)

    def answers(self) -> list[str]:
        return consume_command_file(self._output, uppercase=False)

    def frame(self, token: int) -> tuple[int, int, bytes] | None:
        return self._frames.latest(token)

    def close(self) -> None:
        self._frames.close()
        self._process.terminate()
        try:
            self._process.wait(timeout=_ENDING_S)
        except subprocess.TimeoutExpired:
            self._process.kill()


def waiting_panel() -> Image.Image:
    panel = Image.new("RGBA", LIBRARY_SIZE_PX, (*BG_PRIMARY, 255))
    draw = ImageDraw.Draw(panel)
    font = load_font(_READING_PX)
    x0, y0, x1, y1 = draw.textbbox((0, 0), _READING, font=font)
    width, height = LIBRARY_SIZE_PX
    draw.text(((width - (x1 - x0)) // 2 - x0, (height - (y1 - y0)) // 2 - y0), _READING,
              font=font, fill=(*TEXT_MUTED, 255))
    return panel
