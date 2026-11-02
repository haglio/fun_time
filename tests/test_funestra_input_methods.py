from __future__ import annotations

import subprocess
import sys
import textwrap

_WINDOW_CLASSES_OF_A_FUNESTRA_LIKE_PROCESS = textwrap.dedent("""
    import ctypes
    import ctypes.wintypes as w
    import threading

    from main_funestra.window import leave_out_the_input_methods_that_stall_band_changes

    leave_out_the_input_methods_that_stall_band_changes()
    user32 = ctypes.WinDLL("user32")
    user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, *[ctypes.c_int] * 4,
                                       w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    user32.CreateWindowExW.restype = w.HWND


    def open_a_hidden_window():
        assert user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 1, 1, None, None, None, None)


    open_a_hidden_window()
    # mpv opens its window on a thread of its own, which also has to get none.
    opened, listed = threading.Event(), threading.Event()


    def open_one_and_keep_it():
        open_a_hidden_window()
        opened.set()
        listed.wait()


    threading.Thread(target=open_one_and_keep_it, daemon=True).start()
    opened.wait()
    here = ctypes.WinDLL("kernel32").GetCurrentProcessId()
    classes = []


    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def each(hwnd, _):
        owner = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == here:
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            classes.append(name.value)
        return True


    user32.EnumWindows(each, 0)
    print(",".join(classes))
""")


def test_a_process_that_left_them_out_opens_no_input_method_windows():
    classes = subprocess.run(
        [sys.executable, "-c", _WINDOW_CLASSES_OF_A_FUNESTRA_LIKE_PROCESS],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout.strip().split(",")

    assert classes.count("Static") == 2
    assert not {"IME", "MSCTFIME UI"} & set(classes)
