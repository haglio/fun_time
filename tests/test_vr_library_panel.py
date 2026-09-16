"""The headset's side of its library browser: the desktop's browser, run for it."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from fun_time_vr.library_panel import (
    FRAME_FILENAME,
    HOST_MODULE,
    INPUT_FILENAME,
    LIBRARY_SIZE_PX,
    OUTPUT_FILENAME,
    LibraryHost,
    ShownWhileAsked,
    scroll_from_stick,
    waiting_panel,
)


class TestWhenItIsUp:
    def test_it_opens_once_when_the_session_puts_it_up(self):
        shown = ShownWhileAsked()

        assert shown.asked(True) is True
        assert shown.asked(True) is False
        assert shown.showing

    def test_a_browse_it_put_away_stays_away_until_the_session_puts_it_away_too(self):
        shown = ShownWhileAsked()
        shown.asked(True)
        shown.put_away()

        assert shown.asked(True) is False
        assert not shown.showing

        shown.asked(False)
        assert shown.asked(True) is True
        assert shown.showing


class _FakeProcess:
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.ended: list[str] = []

    def terminate(self):
        self.ended.append("terminated")

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.ended.append("killed")


def _host(tmp_path: Path, launched: list):
    def launch(command, **kwargs):
        launched.append(_FakeProcess(command, **kwargs))
        return launched[-1]

    return LibraryHost(
        manifest_path=tmp_path / "windows_bridge_launch.ini", state_dir=tmp_path,
        python_exe=r"C:\python.exe", launch=launch,
    )


class TestTheBrowserItRuns:
    def test_it_runs_the_desktops_browser_with_no_window_as_live_use(self, tmp_path):
        """Below normal it drew nothing for 90s on a machine busy with test runs."""
        launched: list = []
        host = _host(tmp_path, launched)
        try:
            (process,) = launched
            assert process.command[1:4] == ["-m", HOST_MODULE, str(tmp_path / "windows_bridge_launch.ini")]
            assert process.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
            assert not process.kwargs["creationflags"] & subprocess.BELOW_NORMAL_PRIORITY_CLASS
        finally:
            host.close()

    def test_a_session_starts_with_nothing_left_over_from_the_last(self, tmp_path):
        for name in (INPUT_FILENAME, OUTPUT_FILENAME, FRAME_FILENAME):
            (tmp_path / name).write_text("left over", encoding="utf-8")

        host = _host(tmp_path, [])
        try:
            assert host.answers() == []
            assert not (tmp_path / INPUT_FILENAME).exists()
            assert not (tmp_path / FRAME_FILENAME).exists()
        finally:
            host.close()

    def test_what_it_is_told_goes_to_the_browser_and_what_it_says_comes_back(self, tmp_path):
        host = _host(tmp_path, [])
        try:
            host.send("press 10 20")
            (tmp_path / OUTPUT_FILENAME).write_text("picked C:/videos/Scene One.mp4\n",
                                                    encoding="utf-8")

            assert (tmp_path / INPUT_FILENAME).read_text(encoding="utf-8") == "press 10 20\n"
            assert host.answers() == ["picked C:/videos/Scene One.mp4"]
            assert host.answers() == []
        finally:
            host.close()

    def test_closing_the_session_ends_the_browser(self, tmp_path):
        launched: list = []
        host = _host(tmp_path, launched)

        host.close()

        assert launched[0].ended == ["terminated"]


class TestTheStick:
    def test_a_resting_stick_scrolls_nothing(self):
        assert scroll_from_stick(0.05, 0.1) == 0

    def test_the_stick_pushed_away_scrolls_toward_the_top(self):
        assert scroll_from_stick(1.0, 0.1) > 0 > scroll_from_stick(-1.0, 0.1)


class TestWhileTheLibraryIsBeingRead:
    def test_the_panel_says_so_at_the_browsers_own_size(self):
        panel = np.asarray(waiting_panel())

        assert panel.shape == (LIBRARY_SIZE_PX[1], LIBRARY_SIZE_PX[0], 4)
        assert len(np.unique(panel.reshape(-1, 4), axis=0)) > 1
