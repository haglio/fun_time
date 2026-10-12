"""When Fun Time wakes the headset's runtime, and when it puts it back to sleep."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from app_support.file_channel import write_whole
from app_support.threading_utils import start_daemon_thread

if TYPE_CHECKING:  # the runtime's own imports reach ctypes' Windows surface
    from fun_time_vr.vr_runtime import Probe

HEADSET_READY_NAME = "headset_ready.flag"
OUR_RUNTIME_NAME = "headset_runtime_is_ours.flag"


def headset_ready_flag(state_dir: str | Path) -> Path:
    return Path(state_dir) / HEADSET_READY_NAME


def the_headset_is_ready(state_dir: str | Path) -> bool:
    return headset_ready_flag(state_dir).exists()


def our_runtime_flag(state_dir: str | Path) -> Path:
    return Path(state_dir) / OUR_RUNTIME_NAME


def the_headset_runtime_is_ours(state_dir: str | Path) -> bool:
    return our_runtime_flag(state_dir).exists()


def get_the_headset_ready(state_dir: str | Path) -> Probe:
    from fun_time_vr import windows_sound  # noqa: PLC0415
    from fun_time_vr.vr_runtime import (  # noqa: PLC0415
        Readiness,
        ensure_ready,
        remember_the_sound_devices,
        runtime_was_running,
        what_the_runtime_moved_goes_back,
    )

    ours = not runtime_was_running()
    before = windows_sound.default_devices()
    if ours:
        remember_the_sound_devices(Path(state_dir), before)
    found = ensure_ready()
    moved_to = windows_sound.default_devices()
    what_the_runtime_moved_goes_back(before, moved_to, moved_to)
    if found.readiness is Readiness.READY:
        write_whole(headset_ready_flag(state_dir), "")
        if ours:
            write_whole(our_runtime_flag(state_dir), "")
    return found


def wake_the_headset(
    state_dir: str | Path, *, integration: bool = False,
    start_thread: Callable = start_daemon_thread,
) -> None:
    if integration:
        return
    start_thread(target=get_the_headset_ready, args=(state_dir,), name="headset-wake")


def put_the_headset_to_sleep(state_dir: str | Path, *, crossing: bool) -> bool:
    if crossing or not the_headset_runtime_is_ours(state_dir):
        return False
    from fun_time_vr.vr_runtime import stop_the_runtime_a_session_started  # noqa: PLC0415

    headset_ready_flag(state_dir).unlink(missing_ok=True)
    our_runtime_flag(state_dir).unlink(missing_ok=True)
    stop_the_runtime_a_session_started(Path(state_dir))
    return True
