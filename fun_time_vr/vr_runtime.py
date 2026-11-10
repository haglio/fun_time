from __future__ import annotations

import json
import logging
import subprocess
import time
import winreg
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from app_support.file_channel import write_whole
from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.child_launch import no_child_log
from fun_time.win32_process import process_running

from . import windows_sound
from .windows_sound import Defaults, SoundDevice

logger = logging.getLogger(__name__)

SOUND_DEVICES_BEFORE_NAME = "vr_sound_devices_before.json"

APP_NAME = "Fun Time VR"

STARTUP_TIMEOUT_S = 45.0
POLL_S = 1.0

_OPENXR_KEY = r"SOFTWARE\Khronos\OpenXR\1"

_CLIENTS_UNDER_THE_VENDOR_ROOT = (
    Path("PimaxClient") / "pimaxui" / "PimaxClient.exe",
)
_QUIT_TOOL_BESIDE_THE_REGISTRATION = "launcher.exe"
_SERVICES_THE_CLIENTS_OWN_EXIT_QUITS = ("PiPlatformService", "PiPlayService")
_DISPLAY_SERVER_THAT_KEEPS_THE_HEADSET_ON = "pi_server.exe"
_PI_SERVER_USUALLY_GOES_IN_S = 3.0
QUIT_TIMEOUT_S = 5 * _PI_SERVER_USUALLY_GOES_IN_S

_UNKNOWN_FAILURE = "VR could not be started."

_NO_PIPES_TO_OUTLAST_THE_DEADLINE = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}

class Readiness(Enum):
    READY = "ready"
    NO_RUNTIME = "no_runtime"
    NO_HEADSET = "no_headset"
    FAILED = "failed"


_EXPLANATIONS = {
    Readiness.NO_HEADSET: (
        "No VR headset is answering.\n\n"
        "Power the headset on and connect it, then start Fun Time VR again."
    ),
    Readiness.NO_RUNTIME: (
        "No OpenXR runtime is available.\n\n"
        "Install or start your VR runtime (PimaxXR, SteamVR), then start "
        "Fun Time VR again."
    ),
}


@dataclass(frozen=True)
class Probe:
    readiness: Readiness
    detail: str = ""


def probe() -> Probe:
    try:
        import xr  # noqa: PLC0415  (the loader DLL should load only on VR paths)
    except Exception as exc:
        # A loader that will not *load* fails here rather than at create_instance,
        # and not as an ImportError: pyopenxr raises NotImplementedError off
        # Windows, and LoadLibrary raises OSError where the loader DLL is
        # unusable.  It is a loader failure like any other, so it answers like
        # one -- above the handlers below, it left probe() as a stack trace.
        return Probe(readiness=Readiness.FAILED, detail=str(exc))

    try:
        instance = xr.create_instance(
            xr.InstanceCreateInfo(
                application_info=xr.ApplicationInfo(APP_NAME, 0, "", 0, xr.Version(1, 0, 0)),
            )
        )
    except xr.exception.RuntimeUnavailableError as exc:
        return Probe(readiness=Readiness.NO_RUNTIME, detail=str(exc))
    except Exception as exc:
        return Probe(readiness=Readiness.FAILED, detail=str(exc))

    try:
        xr.get_system(
            instance,
            xr.SystemGetInfo(form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY),
        )
    except xr.exception.FormFactorUnavailableError as exc:
        return Probe(readiness=Readiness.NO_HEADSET, detail=str(exc))
    except Exception as exc:
        return Probe(readiness=Readiness.FAILED, detail=str(exc))
    finally:
        _release_instance(xr, instance)
    return Probe(readiness=Readiness.READY)


def _release_instance(xr: object, instance: object) -> None:
    try:
        xr.destroy_instance(instance)
    except Exception:
        logger.warning("Could not release the probe's OpenXR instance", exc_info=True)


def active_runtime_json() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _OPENXR_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "ActiveRuntime")
    except OSError:
        return None
    return Path(value)


def _vendor_root(runtime_json: Path) -> Path:
    the_runtime_folder_it_registers_in = runtime_json.parent
    return the_runtime_folder_it_registers_in.parent


def launcher_for_runtime(runtime_json: Path) -> Path | None:
    for relative in _CLIENTS_UNDER_THE_VENDOR_ROOT:
        candidate = _vendor_root(runtime_json) / relative
        if candidate.is_file():
            return candidate
    return None


def runtime_launcher() -> Path | None:
    runtime_json = active_runtime_json()
    if runtime_json is None:
        return None
    return launcher_for_runtime(runtime_json)


def start_runtime(launcher: Path) -> None:
    logger.info("Starting VR runtime: %s", launcher)
    subprocess.Popen([str(launcher)], cwd=str(launcher.parent),
                     **no_child_log(), **hidden_subprocess_kwargs())


def runtime_was_running() -> bool:
    return process_running(_DISPLAY_SERVER_THAT_KEEPS_THE_HEADSET_ON)


def runtime_quit_tool() -> Path | None:
    runtime_json = active_runtime_json()
    if runtime_json is None:
        return None
    candidate = runtime_json.parent / _QUIT_TOOL_BESIDE_THE_REGISTRATION
    return candidate if candidate.is_file() else None


def stop_runtime() -> None:
    launcher = runtime_launcher()
    if launcher is not None and process_running(launcher.name):
        logger.info("Stopping VR runtime client: %s", launcher.name)
        _run_quietly(["taskkill", "/IM", launcher.name, "/T", "/F"])
    tool = runtime_quit_tool()
    if tool is None:
        logger.info("No VR runtime quit tool to run")
        return
    for service in _SERVICES_THE_CLIENTS_OWN_EXIT_QUITS:
        logger.info("Quitting VR runtime service: %s", service)
        _run_quietly([str(tool), service, "quit"], cwd=tool.parent)


def remember_the_sound_devices(state_dir: Path, defaults: Defaults | None = None) -> None:
    if defaults is None:
        defaults = windows_sound.default_devices()
    try:
        write_whole(Path(state_dir) / SOUND_DEVICES_BEFORE_NAME, json.dumps({
            f"{flow}/{role}": asdict(device)
            for (flow, role), device in defaults.items()}))
    except OSError:
        logger.warning("Could not write down Windows' default sound devices", exc_info=True)


def _the_sound_devices_remembered(state_dir: Path) -> Defaults:
    remembered = Path(state_dir) / SOUND_DEVICES_BEFORE_NAME
    try:
        saved = json.loads(remembered.read_text(encoding="utf-8"))
        remembered.unlink()
        return {tuple(choice.split("/")): SoundDevice(**device)
                for choice, device in saved.items()}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, AttributeError):
        logger.warning("Could not read back Windows' default sound devices", exc_info=True)
        return {}


def what_the_runtime_moved_goes_back(
        before: Defaults, during: Defaults, after: Defaults) -> None:
    for (flow, role), device in before.items():
        left_on = during.get((flow, role))
        if left_on not in (None, device) and after.get((flow, role)) == left_on:
            logger.info("The VR runtime left Windows' %s %s device on %s; putting back %s",
                        role, flow, left_on.name, device.name)
            windows_sound.make_default(device, role)


def stop_the_runtime_a_session_started(state_dir: Path) -> None:
    before = _the_sound_devices_remembered(state_dir)
    during = windows_sound.default_devices()
    stop_runtime()
    what_the_runtime_moved_goes_back(
        before, during, windows_sound.default_devices())


def _run_quietly(command: list[str], *, cwd: Path | None = None) -> None:
    try:
        ended = subprocess.run(
            command,
            check=False,
            **_NO_PIPES_TO_OUTLAST_THE_DEADLINE,
            timeout=QUIT_TIMEOUT_S,
            cwd=None if cwd is None else str(cwd),
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not run %s", command[0], exc_info=True)
        return
    logger.info("%s ended with exit code %s",
                " ".join([Path(command[0]).name, *command[1:]]), ended.returncode)


def ensure_ready(*, timeout_s: float = STARTUP_TIMEOUT_S, poll_s: float = POLL_S) -> Probe:
    result = probe()
    if result.readiness is Readiness.READY:
        return result

    launcher = runtime_launcher()
    if launcher is None:
        logger.info("No VR runtime launcher to start (%s)", result.readiness.value)
        return result
    if process_running(launcher.name):
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
    lead = _EXPLANATIONS.get(result.readiness, _UNKNOWN_FAILURE)
    return f"{lead}\n\nDetail: {result.detail}"
