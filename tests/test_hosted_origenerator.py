"""The hosted app's bring-up, shared by the desktop session and the headset's."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time import load_config
from fun_time.hosted_origenerator import bring_up_the_hosted_app
from fun_time.manifest import LaunchManifest, write_windows_bridge_manifest
from fun_time.monitors import MonitorInfo
from fun_time.window_layout import screen_layout


def _manifest_for(cfg_factory, tmp_path, overrides):
    config = load_config(cfg_factory(overrides))
    return config, LaunchManifest.read(
        write_windows_bridge_manifest(config, tmp_path / "manifest.ini"))


class TestWhereTheAppsOwnWindowGoes:
    """The one thing a headset session cannot show: the app's main window, which
    shares the Random Favs Browser's rect.  It boots parked there and nothing in
    a headset session restores it, so the session crossing back to the monitors
    is what finds it where it belongs."""

    def test_the_window_takes_the_random_favs_browsers_rect(self, cfg_factory, tmp_path):
        origenerator = tmp_path / "origenerator"
        origenerator.mkdir()
        config, manifest = _manifest_for(
            cfg_factory, tmp_path, {"paths": {"origenerator_dir": str(origenerator)}})
        monitors = [MonitorInfo(0, 0, 2560, 1392), MonitorInfo(2560, 0, 1440, 3440)]
        captured = {}

        with patch("fun_time.window_layout.enumerate_monitors", return_value=monitors), \
             patch("fun_time.hosted_origenerator.launch_origenerator",
                   side_effect=lambda **kw: captured.update(kw) or 91):
            bring_up_the_hosted_app(manifest, project_dirs="")

        with patch("fun_time.window_layout.enumerate_monitors", return_value=monitors):
            expected = screen_layout(config.layout).plan.random_favs_browser
        assert captured["layout_plan"].random_favs_browser == expected

    def test_a_session_hosting_none_never_asks_the_monitors_anything(
            self, cfg_factory, tmp_path):
        """A run with no monitors to read -- the merge gate's, and any headless
        one -- must not fail on a rect for a window it is not opening."""
        _config, manifest = _manifest_for(cfg_factory, tmp_path, {})

        with patch("fun_time.window_layout.enumerate_monitors",
                   side_effect=AssertionError("the monitors were read")):
            assert bring_up_the_hosted_app(manifest, project_dirs="") is None


class TestAdoptingAKeptOrigenerator:
    """A crossing leaves the hosted app running; this is the half that picks it
    up rather than paying for a second boot (docs/entering-vr.md)."""

    def _manifest(self, tmp_path):
        from unittest.mock import MagicMock

        m = MagicMock()
        m.commands.origenerator_status_file = str(tmp_path / "origenerator_status.txt")
        m.commands.origenerator_paused_file = str(tmp_path / "origenerator_paused.txt")
        m.commands.origenerator_cmd_file = str(tmp_path / "origenerator_cmd.txt")
        return m

    def test_a_live_record_is_adopted_and_spent(self, tmp_path: Path):
        from unittest.mock import patch

        from fun_time.hosted_origenerator import _adopt_a_kept_origenerator
        from fun_time.session_handoff import keep_the_origenerator, kept_origenerator

        keep_the_origenerator(tmp_path, pid=6060, created_at=44)
        with patch("fun_time.hosted_origenerator.get_process_creation_time",
                   return_value=44):
            assert _adopt_a_kept_origenerator(self._manifest(tmp_path)).pid == 6060

        assert kept_origenerator(tmp_path) is None, "the record outlived its one use"

    def test_a_recycled_pid_is_never_adopted(self, tmp_path: Path):
        """Windows hands freed pids straight back out, so the creation time is
        what says the process is still the one that was parked."""
        from unittest.mock import patch

        from fun_time.hosted_origenerator import _adopt_a_kept_origenerator
        from fun_time.session_handoff import keep_the_origenerator

        keep_the_origenerator(tmp_path, pid=6060, created_at=44)
        with patch("fun_time.hosted_origenerator.get_process_creation_time",
                   return_value=45):
            assert _adopt_a_kept_origenerator(self._manifest(tmp_path)) is None

    def test_an_ordinary_startup_adopts_nothing(self, tmp_path: Path):
        from fun_time.hosted_origenerator import _adopt_a_kept_origenerator

        assert _adopt_a_kept_origenerator(self._manifest(tmp_path)) is None

    def test_adoption_clears_the_channel_but_never_the_status(self, tmp_path: Path):
        """The app rewrites its status file only when a region changes, so one
        cleared here would stay empty while nothing did."""
        from unittest.mock import patch

        from fun_time.hosted_origenerator import _adopt_a_kept_origenerator
        from fun_time.session_handoff import keep_the_origenerator

        status = tmp_path / "origenerator_status.txt"
        status.write_text("ready\n", encoding="utf-8")
        (tmp_path / "origenerator_cmd.txt").write_text("OPEN_SHOWS\n", encoding="utf-8")
        keep_the_origenerator(tmp_path, pid=6060, created_at=44)

        with patch("fun_time.hosted_origenerator.get_process_creation_time",
                   return_value=44):
            _adopt_a_kept_origenerator(self._manifest(tmp_path))

        assert status.read_text(encoding="utf-8") == "ready\n"
        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8") == ""
