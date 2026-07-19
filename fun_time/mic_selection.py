"""Choose the microphone Fun Time listens on by NAME, not a fragile index.

Windows renumbers audio devices whenever one is added or removed, and it makes
whatever it likes the default input — often a dead virtual mic (a VR headset's
silent input, "Sound Mapper") that hands back pure silence, so Vosk is fed
all-zero audio and no voice command ever fires.  Pinning the listener by device
*index* is exactly what broke voice on this machine: a VR install shifted the
numbering until the pinned index landed on the dead Pimax AirLink mic.  A name
substring ("Brio") survives that renumbering and keeps pointing at the real mic.

:func:`find_input_device` is pure (a device list plus a name) so it is
unit-tested without hardware; only :func:`resolve_input_device` touches
sounddevice.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def find_input_device(devices, name, *, hostapi=None):
    """Return ``(index, device_name)`` of the input whose name contains *name*.

    *name* is matched case-insensitively as a substring.  A match on ``hostapi``
    (the OS default's host API) is preferred — on Windows the same mic is listed
    under several host APIs, and the default's is the one that opens the way we
    capture — but any host API's copy is taken rather than giving up.  Returns
    ``(None, None)`` when *name* is blank or nothing matches.
    """
    want = (name or "").strip().lower()
    if not want:
        return None, None
    matches = [
        (i, d["name"], d.get("hostapi"))
        for i, d in enumerate(devices)
        if d.get("max_input_channels", 0) > 0 and want in d["name"].lower()
    ]
    if not matches:
        return None, None
    on_default_api = [m for m in matches if hostapi is not None and m[2] == hostapi]
    index, device_name, _ = (on_default_api or matches)[0]
    return index, device_name


def resolve_input_device(name):
    """Resolve *name* to a live sounddevice input index via the OS default's
    host API.  Returns ``(index, device_name)``, or ``(None, None)`` if nothing
    matches — in which case the caller lets sounddevice use the system default.
    """
    try:
        import sounddevice as sd  # optional dep (the "voice" extra) — guarded
    except Exception:
        return None, None

    default_input = sd.default.device[0]
    on_api = isinstance(default_input, int) and default_input >= 0
    hostapi = sd.query_devices(default_input)["hostapi"] if on_api else None
    return find_input_device(sd.query_devices(), name, hostapi=hostapi)
