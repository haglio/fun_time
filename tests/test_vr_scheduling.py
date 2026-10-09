from __future__ import annotations

import ctypes
import ctypes.wintypes
import subprocess
import sys
from pathlib import Path

import pytest

from fun_time_vr.scheduling import ahead_of_background_work, scheduled_as_a_game

A_HANDLE = 0x1234
A_PROCESS = 0x5678
ERROR_SERVICE_NOT_ACTIVE = 1062
ERROR_ACCESS_DENIED = 5
REALTIME = 5
ABOVE_NORMAL = 3
STATUS_PRIVILEGE_NOT_HELD = -1073741727  # 0xC0000061, as NTSTATUS arrives signed


class _FakeFunction:
    def __init__(self, result: object = 0) -> None:
        self.result = result
        self.argtypes: object = None
        self.restype: object = None
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class _FakeAvrt:
    def __init__(self, handle: object = A_HANDLE) -> None:
        self.AvSetMmThreadCharacteristicsW = _FakeFunction(handle)
        self.AvRevertMmThreadCharacteristics = _FakeFunction(1)


class _ReadsBack(_FakeFunction):
    def __init__(self, value: int) -> None:
        super().__init__(0)
        self.value = value

    def __call__(self, process, out):
        self.calls.append((process, out))
        out._obj.value = self.value
        return self.result


class _FakeGdi32:
    def __init__(self, found: int = ABOVE_NORMAL) -> None:
        self.D3DKMTGetProcessSchedulingPriorityClass = _ReadsBack(found)
        self.D3DKMTSetProcessSchedulingPriorityClass = _FakeFunction(0)


class _FakeKernel32:
    def __init__(self, found: int = subprocess.NORMAL_PRIORITY_CLASS) -> None:
        self.GetPriorityClass = _FakeFunction(found)
        self.SetPriorityClass = _FakeFunction(1)


def _ahead(avrt=None, gdi32=None, kernel32=None, **kwargs):
    avrt = avrt or _FakeAvrt()
    gdi32 = gdi32 or _FakeGdi32()
    kernel32 = kernel32 or _FakeKernel32()
    return ahead_of_background_work(
        avrt=lambda: avrt, gdi32=lambda: gdi32, kernel32=lambda: kernel32,
        this_process=lambda: A_PROCESS, **kwargs)


def test_the_block_runs_with_this_process_above_normal_priority_as_genau_runs():
    kernel32 = _FakeKernel32()

    with _ahead(kernel32=kernel32):
        assert kernel32.SetPriorityClass.calls == [
            (A_PROCESS, subprocess.ABOVE_NORMAL_PRIORITY_CLASS)]


def test_leaving_the_block_puts_the_process_priority_back_as_it_was_found():
    kernel32 = _FakeKernel32(found=subprocess.BELOW_NORMAL_PRIORITY_CLASS)

    with _ahead(kernel32=kernel32):
        pass

    assert kernel32.SetPriorityClass.calls[-1] == (
        A_PROCESS, subprocess.BELOW_NORMAL_PRIORITY_CLASS)


def test_a_refused_processor_priority_runs_the_block_anyway_and_says_why(caplog):
    kernel32 = _FakeKernel32()
    kernel32.SetPriorityClass.result = 0
    ran = []

    with _ahead(kernel32=kernel32, last_error=lambda: ERROR_ACCESS_DENIED):
        ran.append(True)

    assert ran == [True]
    assert kernel32.SetPriorityClass.calls == [
        (A_PROCESS, subprocess.ABOVE_NORMAL_PRIORITY_CLASS)]
    (record,) = caplog.records
    assert str(ERROR_ACCESS_DENIED) in record.getMessage()


_PRIORITY_CLASS_INSIDE_THE_BLOCK = """
import ctypes, ctypes.wintypes
from fun_time_vr.scheduling import ahead_of_background_work
kernel32 = ctypes.WinDLL("kernel32")
kernel32.GetPriorityClass.argtypes = [ctypes.wintypes.HANDLE]
with ahead_of_background_work():
    print(kernel32.GetPriorityClass(ctypes.wintypes.HANDLE(-1)))
"""


def test_a_real_headset_process_runs_above_normal_priority_inside_the_block():
    result = subprocess.run(
        [sys.executable, "-c", _PRIORITY_CLASS_INSIDE_THE_BLOCK],
        cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert int(result.stdout) == subprocess.ABOVE_NORMAL_PRIORITY_CLASS


def test_the_process_goes_to_the_priority_calls_as_a_handle():
    kernel32 = _FakeKernel32()

    with _ahead(kernel32=kernel32):
        pass

    read, write = kernel32.GetPriorityClass, kernel32.SetPriorityClass
    assert read.argtypes == [ctypes.wintypes.HANDLE]
    assert write.argtypes == [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    assert read.restype is ctypes.wintypes.DWORD
    assert write.restype is ctypes.wintypes.BOOL


def test_the_block_runs_registered_as_a_game_thread():
    avrt = _FakeAvrt()

    with _ahead(avrt):
        (call,) = avrt.AvSetMmThreadCharacteristicsW.calls

    assert call[0] == "Games"


def test_the_block_runs_with_this_process_first_on_the_graphics_card():
    gdi32 = _FakeGdi32()

    with _ahead(gdi32=gdi32):
        assert gdi32.D3DKMTSetProcessSchedulingPriorityClass.calls == [(A_PROCESS, REALTIME)]


def test_leaving_the_block_puts_the_graphics_card_back_as_it_was_found():
    gdi32 = _FakeGdi32(found=ABOVE_NORMAL)

    with _ahead(gdi32=gdi32):
        pass

    assert gdi32.D3DKMTSetProcessSchedulingPriorityClass.calls == [
        (A_PROCESS, REALTIME), (A_PROCESS, ABOVE_NORMAL)]


def test_leaving_the_block_gives_the_registration_back():
    avrt = _FakeAvrt()

    with _ahead(avrt):
        assert not avrt.AvRevertMmThreadCharacteristics.calls

    assert avrt.AvRevertMmThreadCharacteristics.calls == [(A_HANDLE,)]


def test_a_block_that_raises_still_gives_everything_back():
    avrt = _FakeAvrt()
    gdi32 = _FakeGdi32(found=ABOVE_NORMAL)

    with pytest.raises(RuntimeError), _ahead(avrt, gdi32):
        raise RuntimeError("the session ended badly")

    assert avrt.AvRevertMmThreadCharacteristics.calls == [(A_HANDLE,)]
    assert gdi32.D3DKMTSetProcessSchedulingPriorityClass.calls[-1] == (A_PROCESS, ABOVE_NORMAL)


def test_the_registration_handle_comes_back_whole_and_goes_back_as_a_handle():
    avrt = _FakeAvrt()

    with _ahead(avrt):
        pass

    register = avrt.AvSetMmThreadCharacteristicsW
    revert = avrt.AvRevertMmThreadCharacteristics
    assert register.restype is ctypes.wintypes.HANDLE
    assert register.argtypes == [ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
    assert revert.argtypes == [ctypes.wintypes.HANDLE]
    assert revert.restype is ctypes.wintypes.BOOL


def test_the_process_goes_to_the_graphics_calls_as_a_handle_and_comes_back_a_status():
    gdi32 = _FakeGdi32()

    with _ahead(gdi32=gdi32):
        pass

    read = gdi32.D3DKMTGetProcessSchedulingPriorityClass
    write = gdi32.D3DKMTSetProcessSchedulingPriorityClass
    assert read.argtypes == [ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.c_int)]
    assert write.argtypes == [ctypes.wintypes.HANDLE, ctypes.c_int]
    assert read.restype is ctypes.c_long
    assert write.restype is ctypes.c_long


def test_a_refused_graphics_priority_runs_the_block_anyway_and_says_why(caplog):
    gdi32 = _FakeGdi32()
    gdi32.D3DKMTSetProcessSchedulingPriorityClass.result = STATUS_PRIVILEGE_NOT_HELD
    ran = []

    with _ahead(gdi32=gdi32):
        ran.append(True)

    assert ran == [True]
    assert gdi32.D3DKMTSetProcessSchedulingPriorityClass.calls == [(A_PROCESS, REALTIME)]
    (record,) = caplog.records
    assert "0xc0000061" in record.getMessage()


def test_a_refused_registration_runs_the_block_anyway_and_says_why(caplog):
    avrt = _FakeAvrt(handle=None)
    ran = []

    with _ahead(avrt, last_error=lambda: ERROR_SERVICE_NOT_ACTIVE):
        ran.append(True)

    assert ran == [True]
    assert not avrt.AvRevertMmThreadCharacteristics.calls
    (record,) = caplog.records
    assert str(ERROR_SERVICE_NOT_ACTIVE) in record.getMessage()


def test_a_video_thread_runs_registered_as_a_game_thread_and_gives_it_back():
    avrt = _FakeAvrt()

    with scheduled_as_a_game(avrt=lambda: avrt):
        (call,) = avrt.AvSetMmThreadCharacteristicsW.calls

    assert call[0] == "Games"
    assert avrt.AvRevertMmThreadCharacteristics.calls == [(A_HANDLE,)]
