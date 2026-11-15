from __future__ import annotations

import logging
from types import SimpleNamespace

from fun_time_vr.windows_sound import SoundDevice, default_devices, make_default

_SPEAKERS = SimpleNamespace(id="{made-up-speakers}", name="Example Speakers")
_HEADSET = SimpleNamespace(id="{made-up-headset}", name="Example Headset")
_MICROPHONE = SimpleNamespace(id="{made-up-mic}", name="Example Microphone")

_OUTPUT, _INPUT = 0, 1
_CONSOLE, _MULTIMEDIA, _COMMUNICATIONS = 0, 1, 2


class _Enumerator:
    def __init__(self, defaults) -> None:
        self._defaults = defaults

    def GetDefaultAudioEndpoint(self, flow: int, role: int):  # noqa: N802
        if (flow, role) not in self._defaults:
            raise OSError("Windows has no default here")
        return self._defaults[flow, role]


class _Policy:
    def __init__(self, made: list) -> None:
        self._made = made

    def SetDefaultEndpoint(self, device_id: str, role: int) -> None:  # noqa: N802
        self._made.append((device_id, role))


class _Windows:
    FAILURES = (OSError,)

    def __init__(self, defaults=None, *, refuses: bool = False) -> None:
        self._defaults = defaults or {}
        self._refuses = refuses
        self.made: list[tuple[str, int]] = []

    def open_device_enumerator(self):
        if self._refuses:
            raise OSError("COM is not answering")
        return _Enumerator(self._defaults)

    def open_policy_config(self):
        if self._refuses:
            raise OSError("COM is not answering")
        return _Policy(self.made)

    @staticmethod
    def id_of(device) -> str:
        return device.id

    @staticmethod
    def name_of(device) -> str:
        return device.name


def test_each_default_windows_holds_is_read_by_flow_and_role():
    windows = _Windows({(_OUTPUT, _CONSOLE): _SPEAKERS, (_OUTPUT, _COMMUNICATIONS): _HEADSET,
                        (_INPUT, _MULTIMEDIA): _MICROPHONE})

    assert default_devices(windows) == {
        ("output", "console"): SoundDevice("{made-up-speakers}", "Example Speakers"),
        ("output", "communications"): SoundDevice("{made-up-headset}", "Example Headset"),
        ("input", "multimedia"): SoundDevice("{made-up-mic}", "Example Microphone"),
    }


def test_a_flow_and_role_windows_has_no_device_for_is_left_out():
    assert default_devices(_Windows({(_OUTPUT, _MULTIMEDIA): _SPEAKERS})) == {
        ("output", "multimedia"): SoundDevice("{made-up-speakers}", "Example Speakers")}


def test_windows_refusing_to_answer_reads_as_no_defaults(caplog):
    with caplog.at_level(logging.WARNING, logger="fun_time_vr.windows_sound"):
        assert default_devices(_Windows(refuses=True)) == {}

    assert "Could not ask Windows for its default sound devices" in caplog.text


def test_a_device_is_made_the_default_for_the_role_asked_by_windows_numbering():
    windows = _Windows()

    make_default(SoundDevice("{made-up-headset}", "Example Headset"), "communications", windows)

    assert windows.made == [("{made-up-headset}", _COMMUNICATIONS)]


def test_windows_refusing_the_default_is_written_down_and_nothing_breaks(caplog):
    with caplog.at_level(logging.WARNING, logger="fun_time_vr.windows_sound"):
        make_default(SoundDevice("{made-up-headset}", "Example Headset"), "console",
                     _Windows(refuses=True))

    assert "Could not make Example Headset Windows' default for console" in caplog.text
