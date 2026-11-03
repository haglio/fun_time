from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fun_time.manifest import LaunchManifest

    from .funestras import VrSettings
    from .perf import FramePerf


@dataclass(frozen=True)
class RoomLaunch:
    manifest: LaunchManifest
    vr: VrSettings
    contexts: Any
    perf: FramePerf | None = None
