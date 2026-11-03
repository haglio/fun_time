from __future__ import annotations

from typing import Any, NamedTuple

from .perf import FramePerf
from .render import SceneRenderer


class Headset(NamedTuple):
    session: Any
    renderer: SceneRenderer
    perf: FramePerf
