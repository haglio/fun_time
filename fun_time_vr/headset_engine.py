# A sink parked on the stand takes the stream without draining it, and mpv's clock follows audio, so
# an engine heard before the compositor presents freezes on frame 1.
from __future__ import annotations

from collections.abc import Callable

from funestra.render_engine import MpvRenderEngine


class HeadsetEngine(MpvRenderEngine):
    def __init__(self, get_proc_address: Callable[[str], int | None], *,
                 loop_file: bool, prefetch: bool) -> None:
        super().__init__(get_proc_address, muted=True, loop_file=loop_file, prefetch=prefetch)
        self._sound_live = False
        self._muted_asked = True

    def set_muted(self, muted: bool) -> None:
        self._muted_asked = muted
        if self._sound_live:
            super().set_muted(muted)

    def radio_station(self, videos):
        return super().radio_station(videos) if self._sound_live else None

    def sound_goes_live(self, device: str) -> str | None:
        if self._sound_live:
            return None
        self._sound_live = True
        picked = self.set_audio_device_matching(device) if device else None
        super().set_muted(self._muted_asked)
        return picked
