from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest

from fun_time_vr import vr_runtime, windows_sound

WHAT_REACHES_HIS_HEADSET = {
    (vr_runtime, "probe"): "asked the VR runtime for a headset",
    (vr_runtime, "start_runtime"): "started the VR runtime",
    (vr_runtime, "stop_runtime"): "stopped the VR runtime",
    (windows_sound, "make_default"): "changed Windows' default sound device",
}


@contextlib.contextmanager
def his_headset_refused(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reached_from_any_thread: list[str] = []

    def refuse(what):
        def refused(*_args, **_kwargs):
            reached_from_any_thread.append(what)
            raise RuntimeError(f"a unit test {what}")
        return refused

    for (module, name), what in WHAT_REACHES_HIS_HEADSET.items():
        monkeypatch.setattr(module, name, refuse(what))
    yield
    if reached_from_any_thread:
        pytest.fail(f"a unit test reached his real headset: {', '.join(reached_from_any_thread)}")
