"""Tests for name-based microphone selection.

The recognizer is only as good as the device it listens on.  Windows renumbers
devices and makes a dead virtual mic (a VR headset's silent input, "Sound
Mapper") the default input, handing back pure silence — so Vosk hears nothing
and no command fires.  Pinning by a name substring survives the renumbering.
``find_input_device`` is pure (a device list + a name) so it is tested here
without any hardware.
"""
from __future__ import annotations

from fun_time.mic_selection import find_input_device


def _dev(name: str, *, inputs: int = 2, hostapi: int = 0) -> dict:
    return {"name": name, "max_input_channels": inputs, "hostapi": hostapi}


def test_matches_a_case_insensitive_substring():
    devices = [_dev("Microphone (Pimax AirLink)"), _dev("Microphone (Brio 101)")]

    assert find_input_device(devices, "brio") == (1, "Microphone (Brio 101)")


def test_ignores_output_only_devices():
    devices = [_dev("Brio Speakers", inputs=0), _dev("Microphone (Brio 101)")]

    assert find_input_device(devices, "brio") == (1, "Microphone (Brio 101)")


def test_prefers_the_match_on_the_default_host_api():
    devices = [
        _dev("Microphone (Brio 101)", hostapi=2),  # WASAPI copy, listed first
        _dev("Microphone (Brio 101)", hostapi=0),  # the default host API's copy
    ]

    assert find_input_device(devices, "brio", hostapi=0) == (1, "Microphone (Brio 101)")


def test_falls_back_to_any_host_api_when_none_on_the_default():
    devices = [_dev("Microphone (Pimax)", hostapi=0), _dev("Microphone (Brio 101)", hostapi=2)]

    assert find_input_device(devices, "brio", hostapi=0) == (1, "Microphone (Brio 101)")


def test_no_match_returns_none():
    devices = [_dev("Microphone (Pimax AirLink)"), _dev("Microphone (Realtek Audio)")]

    assert find_input_device(devices, "brio") == (None, None)


def test_blank_name_returns_none():
    devices = [_dev("Microphone (Brio 101)")]

    assert find_input_device(devices, "") == (None, None)
    assert find_input_device(devices, "   ") == (None, None)
    assert find_input_device(devices, None) == (None, None)


def test_takes_the_first_match_when_several_share_the_host_api():
    devices = [
        _dev("Brio 101 Mic A", hostapi=0),
        _dev("Brio 101 Mic B", hostapi=0),
    ]

    assert find_input_device(devices, "brio", hostapi=0) == (0, "Brio 101 Mic A")
