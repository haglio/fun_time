from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import subprocess
from unittest.mock import MagicMock

import pytest

from main_funestra.funestra_priority import ahead_on_the_processor

THIS_PROCESS = ctypes.wintypes.HANDLE(-1).value
ERROR_ACCESS_DENIED = 5


def _kernel32(*, found: int = subprocess.NORMAL_PRIORITY_CLASS, allowed: bool = True):
    kernel32 = MagicMock()
    kernel32.GetPriorityClass.return_value = found
    kernel32.SetPriorityClass.return_value = 1 if allowed else 0
    return kernel32


def _priority_set_last(kernel32) -> tuple[int, int]:
    (handle, priority), _kwargs = kernel32.SetPriorityClass.call_args
    return handle.value, priority


def test_the_funestra_plays_above_normal_priority():
    kernel32 = _kernel32()

    with ahead_on_the_processor(kernel32):
        assert _priority_set_last(kernel32) == (
            THIS_PROCESS, subprocess.ABOVE_NORMAL_PRIORITY_CLASS)


def test_leaving_the_block_puts_the_priority_back_as_it_was_found():
    kernel32 = _kernel32(found=subprocess.BELOW_NORMAL_PRIORITY_CLASS)

    with ahead_on_the_processor(kernel32):
        pass

    assert _priority_set_last(kernel32) == (THIS_PROCESS, subprocess.BELOW_NORMAL_PRIORITY_CLASS)


def test_a_funestra_that_dies_still_puts_the_priority_back():
    kernel32 = _kernel32(found=subprocess.BELOW_NORMAL_PRIORITY_CLASS)

    with pytest.raises(RuntimeError), ahead_on_the_processor(kernel32):
        raise RuntimeError("the Funestra died")

    assert _priority_set_last(kernel32) == (THIS_PROCESS, subprocess.BELOW_NORMAL_PRIORITY_CLASS)


def test_a_refusal_is_said_with_windows_reason(caplog):
    with caplog.at_level(logging.WARNING, logger="main_funestra.funestra_priority"):
        with ahead_on_the_processor(_kernel32(allowed=False), lambda: ERROR_ACCESS_DENIED):
            pass

    assert f"error {ERROR_ACCESS_DENIED}" in caplog.text


def test_a_refused_priority_still_plays_and_puts_nothing_back():
    kernel32 = _kernel32(allowed=False)
    played = []

    with ahead_on_the_processor(kernel32, lambda: ERROR_ACCESS_DENIED):
        played.append(True)

    assert played == [True]
    assert kernel32.SetPriorityClass.call_count == 1
