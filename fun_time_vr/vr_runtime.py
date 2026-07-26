"""Is the VR runtime ready — and if not, can we bring it up ourselves?

FunTimeVR launches hidden from a shortcut, so a startup that dies on its way
to the headset leaves nothing on screen to read.  Asking this question before
launching any player keeps the failure fast and the answer specific: no
runtime at all, a runtime whose headset is off, or ready to render.

Adapted from GenauVR's proven probe (genau_vr.vr_runtime), with ``xr``
imported lazily so the pure callers of this package never pay for the OpenXR
loader; consolidating the two copies into a shared sibling is part of the
planned GenauVR-engine extraction.
"""
from __future__ import annotations

import logging
import subprocess
import time
import winreg
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)

APP_NAME = "FunTimeVR"

# A cold VR runtime takes its time: the client starts, brings up its service,
# and only then does the headset answer. Poll rather than guess at a fixed wait.
STARTUP_TIMEOUT_S = 45.0
POLL_S = 1.0

_OPENXR_KEY = r"SOFTWARE\Khronos\OpenXR\1"

# An OpenXR runtime registers itself at <vendor root>/Runtime/<name>.json, while
# the thing a user starts to bring that runtime up lives elsewhere under the same
# root. Only runtimes we can find a launcher for get started for the user;
# anything else is reported so they can start it themselves.
_LAUNCHER_RELATIVE_PATHS = (
    Path("PimaxClient") / "pimaxui" / "PimaxClient.exe",
)

_UNKNOWN_FAILURE = "VR could not be started."

_EXPLANATIONS = {
    "no_headset": (
        "No VR headset is answering.\n\n"
        "Power the headset on and connect it, then start FunTimeVR again."
    ),
    "no_runtime": (
        "No OpenXR runtime is available.\n\n"
        "Install or start your VR runtime (PimaxXR, SteamVR), then start "
        "FunTimeVR again."
    ),
}


class Readiness(Enum):
    """How far the OpenXR stack got before it stopped answering."""

    READY = "ready"
    NO_RUNTIME = "no_runtime"
    NO_HEADSET = "no_headset"
    FAILED = "failed"


@dataclass(frozen=True)
class Probe:
    """What a single look at the OpenXR stack found."""

    readiness: Readiness
    detail: str = ""


def probe() -> Probe:
    """Ask OpenXR for a head-mounted display, without opening a window."""
    import xr  # noqa: PLC0415 — the loader DLL should load only on VR paths

    try:
        instance = xr.create_instance(
            xr.InstanceCreateInfo(
                application_info=xr.ApplicationInfo(APP_NAME, 0, "", 0, xr.Version(1, 0, 0)),
            )
        )
    except xr.exception.RuntimeUnavailableError as exc:
        return Probe(readiness=Readiness.NO_RUNTIME, detail=str(exc))
    except Exception as exc:  # every loader failure has to reach the popup
        return Probe(readiness=Readiness.FAILED, detail=str(exc))

    try:
        xr.get_system(
            instance,
            xr.SystemGetInfo(form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY),
        )
    except xr.exception.FormFactorUnavailableError as exc:
        return Probe(readiness=Readiness.NO_HEADSET, detail=str(exc))
    except Exception as exc:  # likewise for anything the runtime itself raises
        return Probe(readiness=Readiness.FAILED, detail=str(exc))
    finally:
        xr.destroy_instance(instance)
    return Probe(readiness=Readiness.READY)


def active_runtime_json() -> Path | None:
    """Where Windows says the current OpenXR runtime is registered."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _OPENXR_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "ActiveRuntime")
    except OSError:
        return None
    return Path(value)


def launcher_for_runtime(runtime_json: Path) -> Path | None:
    """The executable that brings up the runtime registered at *runtime_json*."""
    vendor_root = runtime_json.parent.parent
    for relative in _LAUNCHER_RELATIVE_PATHS:
        candidate = vendor_root / relative
        if candidate.is_file():
            return candidate
    return None


def runtime_launcher() -> Path | None:
    """The executable that brings up whichever OpenXR runtime is active."""
    runtime_json = active_runtime_json()
    if runtime_json is None:
        return None
    return launcher_for_runtime(runtime_json)


def is_running(executable: Path) -> bool:
    """Whether a process with *executable*'s file name is already running."""
    try:
        output = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {executable.name}", "/NH", "/FO", "CSV"],
            text=True,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return executable.name.lower() in output.lower()


def start_runtime(launcher: Path) -> None:
    """Start the VR runtime's own client, the way its desktop shortcut would."""
    logger.info("Starting VR runtime: %s", launcher)
    subprocess.Popen([str(launcher)], cwd=str(launcher.parent), **hidden_subprocess_kwargs())


def ensure_ready(*, timeout_s: float = STARTUP_TIMEOUT_S, poll_s: float = POLL_S) -> Probe:
    """Get VR to a state FunTimeVR can render into, starting the runtime if needed.

    A runtime that is already up and still has no headset for us is reported
    straight back: restarting its client cannot power on a headset, and waiting
    out the timeout would only delay saying so.
    """
    result = probe()
    if result.readiness is Readiness.READY:
        return result

    launcher = runtime_launcher()
    if launcher is None:
        logger.info("No VR runtime launcher to start (%s)", result.readiness.value)
        return result
    if is_running(launcher):
        logger.info("VR runtime already running, but %s", result.readiness.value)
        return result

    start_runtime(launcher)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        result = probe()
        if result.readiness is Readiness.READY:
            logger.info("VR runtime came up")
            return result
    logger.warning(
        "VR runtime did not come up within %.0fs (%s)", timeout_s, result.readiness.value
    )
    return result


def explain(result: Probe) -> str:
    """The popup text for a probe that did not end in a headset.

    Falls back rather than raising on a readiness it has no wording for: this
    runs on the error path, and a crash here is the silent failure it replaces.
    """
    lead = _EXPLANATIONS.get(result.readiness.value, _UNKNOWN_FAILURE)
    return f"{lead}\n\nDetail: {result.detail}"
