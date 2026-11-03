from __future__ import annotations


class AMark:
    def __init__(self, gpu) -> None:
        self._gpu = gpu
        self.set_times = 0

    def set(self) -> None:
        self.set_times += 1

    @property
    def reached(self) -> bool:
        return self._gpu.reaches_marks

    def close(self) -> None:
        self._gpu.closed_marks += 1


class AGpu:
    def __init__(self, *, reaches_marks: bool = True) -> None:
        self.reaches_marks = reaches_marks
        self.flushes = 0
        self.copied: list[tuple] = []
        self.closed_marks = 0

    def mark(self) -> AMark:
        return AMark(self)

    def flush(self) -> None:
        self.flushes += 1

    def copy(self, frame, target) -> None:
        self.copied.append((frame.texture, target.texture))

    def read_pixels(self, target):
        return ("pixels", target.width, target.height)


class AnEngine:
    def __init__(self, *, dims=(1920, 1080), video="a.mp4") -> None:
        self.video_dims = dims
        self.has_frame_to_draw = True
        self.position_ms = 0.0
        self.rendered: list[tuple] = []
        self._video = video

    def render(self, fbo, width, height, *, flip_y=False):
        self.rendered.append((fbo, width, height, flip_y))
        return self._video


class ATarget:
    def __init__(self, number: int) -> None:
        self.fbo, self.texture = 10 + number, 20 + number
        self.width = self.height = 0
        self.painted = False
        self.video = None

    def ensure(self, width: int, height: int) -> None:
        self.width, self.height = width, height
