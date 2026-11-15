from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from . import sound_endpoints

logger = logging.getLogger(__name__)


FLOWS = ("output", "input")  # Windows' EDataFlow, numbered in this order
ROLES = ("console", "multimedia", "communications")


@dataclass(frozen=True)
class SoundDevice:
    id: str
    name: str


Defaults = dict[tuple[str, str], SoundDevice]


def _default_device_or_none(windows, enumerator, flow: str, role: str) -> SoundDevice | None:
    try:
        device = enumerator.GetDefaultAudioEndpoint(FLOWS.index(flow), ROLES.index(role))
        return SoundDevice(windows.id_of(device), windows.name_of(device))
    except windows.FAILURES:
        return None


def default_devices(windows: Any = sound_endpoints) -> Defaults:
    try:
        enumerator = windows.open_device_enumerator()
    except windows.FAILURES:
        logger.warning("Could not ask Windows for its default sound devices", exc_info=True)
        return {}
    return {(flow, role): device for flow in FLOWS for role in ROLES
            if (device := _default_device_or_none(windows, enumerator, flow, role)) is not None}


def make_default(device: SoundDevice, role: str, windows: Any = sound_endpoints) -> None:
    try:
        windows.open_policy_config().SetDefaultEndpoint(device.id, ROLES.index(role))
    except windows.FAILURES:
        logger.warning("Could not make %s Windows' default for %s",
                       device.name, role, exc_info=True)
