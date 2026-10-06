"""The hosted app's bring-up, shared by the desktop session and the headset's."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from player_core.file_channel import consume_command_file

from fun_time import load_config
from fun_time.hosted_origenerator import (
    HAND_OVER,
    TAKE_BACK,
    _adopt_a_kept_origenerator,
    bring_up_the_hosted_app,
)
from fun_time.manifest import LaunchManifest, write_windows_bridge_manifest
from fun_time.monitors import MonitorInfo
from fun_time.session_handoff import keep_the_origenerator, kept_origenerator
from fun_time.standalone_origenerator import OpenOrigenerator
from fun_time.window_layout import WindowLayoutPlan, WindowRect, screen_layout
from fun_time.windows_bridge_startup import launch_origenerator, origenerator_session_args

_A_PLAN = WindowLayoutPlan(
    portrait=WindowRect(0, 0, 10, 10), landscape=WindowRect(0, 0, 10, 10),
    dashboard=WindowRect(0, 0, 10, 10), random_favs_browser=WindowRect(1, 2, 30, 40))


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


class TestHandingTheWindowToTheHeadset:
    """A headset session has no monitor to put the app's window on, so it asks
    for the window's picture instead and sends its pointer's presses back."""

    def _launch(self, cfg_factory, tmp_path, *, in_a_headset):
        origenerator = tmp_path / "origenerator"
        origenerator.mkdir()
        config, manifest = _manifest_for(
            cfg_factory, tmp_path, {"paths": {"origenerator_dir": str(origenerator)}})
        monitors = [MonitorInfo(0, 0, 2560, 1392), MonitorInfo(2560, 0, 1440, 3440)]
        captured = {}
        looking = patch("fun_time.window_layout.enumerate_monitors",
                        return_value=monitors)
        launching = patch("fun_time.hosted_origenerator.launch_origenerator",
                          side_effect=lambda **kw: captured.update(kw) or 91)
        with looking, launching:
            bring_up_the_hosted_app(manifest, project_dirs="", in_a_headset=in_a_headset)
        return config, captured

    def test_a_headset_session_names_both_files(self, cfg_factory, tmp_path):
        config, captured = self._launch(cfg_factory, tmp_path, in_a_headset=True)

        assert captured["frames_file"] == str(config.origenerator_frames_file)
        assert captured["input_file"] == str(config.origenerator_input_file)

    def test_a_session_on_the_monitors_names_neither(self, cfg_factory, tmp_path):
        """The window itself is what is seen there, and a picture written every
        tick for nobody is a grab of a window every tick for nothing."""
        _config, captured = self._launch(cfg_factory, tmp_path, in_a_headset=False)

        assert captured["frames_file"] is None
        assert captured["input_file"] is None

    def test_the_real_launch_carries_the_pair_through(self, cfg_factory, tmp_path):
        """Through `launch_origenerator` itself rather than a stand-in for it:
        a stand-in that takes anything cannot fail on an argument the real one
        does not accept, which is how the pair reached it and killed the launch
        (tests/integration/test_origenerator_mode_integration.py caught it)."""
        started = {}

        class _Started:
            pid = 91

        with patch("fun_time.windows_bridge_startup.subprocess.Popen",
                   side_effect=lambda cmd, **kw: started.update(cmd=cmd) or _Started()):
            launch_origenerator(
                python_exe="py.exe", origenerator_dir=tmp_path, layout_plan=_A_PLAN,
                command_file="c", paused_file="p", status_file="s",
                dashboard_cmd_file="d", players={},
                frames_file="st/origenerator_frame.bin",
                input_file="st/origenerator_input.txt",
            )

        assert "--frames-file" in started["cmd"]
        assert "--input-file" in started["cmd"]

    def test_the_flags_are_the_ones_the_app_declares(self, cfg_factory, tmp_path):
        """The app publishes its launch contract because neither repo may import
        the other; these are the two flags of it this pair is written as."""
        argv = origenerator_session_args(
            layout_plan=_A_PLAN, command_file="c", paused_file="p", status_file="s",
            dashboard_cmd_file="d", players={},
            frames_file="st/origenerator_frame.bin",
            input_file="st/origenerator_input.txt",
        )

        assert argv[argv.index("--frames-file") + 1] == "st/origenerator_frame.bin"
        assert argv[argv.index("--input-file") + 1] == "st/origenerator_input.txt"


class TestTellingAKeptAppWhereItsWindowGoes:
    """A crossing keeps the app running, and it was started for the session it
    left: the one adopting it says on the command file which shape it is."""

    def _adopt(self, cfg_factory, tmp_path, *, in_a_headset):
        origenerator = tmp_path / "origenerator"
        origenerator.mkdir()
        config, manifest = _manifest_for(
            cfg_factory, tmp_path, {"paths": {"origenerator_dir": str(origenerator)}})
        keep_the_origenerator(config.paths.state_dir, pid=6060, created_at=44)
        monitors = [MonitorInfo(0, 0, 2560, 1392), MonitorInfo(2560, 0, 1440, 3440)]
        looking = patch("fun_time.window_layout.enumerate_monitors", return_value=monitors)
        alive = patch("fun_time.hosted_origenerator.get_process_creation_time",
                      return_value=44)
        with looking, alive:
            app = bring_up_the_hosted_app(manifest, project_dirs="", in_a_headset=in_a_headset)
        assert app is not None and app.already_open
        return config, consume_command_file(config.origenerator_cmd_file, uppercase=False)

    def test_a_headset_session_asks_for_the_picture(self, cfg_factory, tmp_path):
        config, said = self._adopt(cfg_factory, tmp_path, in_a_headset=True)

        assert said == [f"{HAND_OVER}|{config.origenerator_frames_file}"
                        f"|{config.origenerator_input_file}"]

    def test_a_desktop_session_takes_the_window_back(self, cfg_factory, tmp_path):
        """One the headset had shown is still publishing and still up; here it
        parks, as a hosted window boots, and writes no picture for nobody."""
        _config, said = self._adopt(cfg_factory, tmp_path, in_a_headset=False)

        assert said == [TAKE_BACK]


class TestAdoptingAKeptOrigenerator:
    """A crossing leaves the hosted app running; this is the half that picks it
    up rather than paying for a second boot (docs/entering-vr.md)."""

    def _manifest(self, tmp_path):
        m = MagicMock()
        m.commands.origenerator_status_file = str(tmp_path / "origenerator_status.txt")
        m.commands.origenerator_paused_file = str(tmp_path / "origenerator_paused.txt")
        m.commands.origenerator_cmd_file = str(tmp_path / "origenerator_cmd.txt")
        return m

    def test_a_live_record_is_adopted_and_spent(self, tmp_path: Path):
        keep_the_origenerator(tmp_path, pid=6060, created_at=44)
        with patch("fun_time.hosted_origenerator.get_process_creation_time",
                   return_value=44):
            assert _adopt_a_kept_origenerator(self._manifest(tmp_path)).pid == 6060

        assert kept_origenerator(tmp_path) is None, "the record outlived its one use"

    def test_a_recycled_pid_is_never_adopted(self, tmp_path: Path):
        """Windows hands freed pids straight back out, so the creation time is
        what says the process is still the one that was parked."""
        keep_the_origenerator(tmp_path, pid=6060, created_at=44)
        with patch("fun_time.hosted_origenerator.get_process_creation_time",
                   return_value=45):
            assert _adopt_a_kept_origenerator(self._manifest(tmp_path)) is None

    def test_an_ordinary_startup_adopts_nothing(self, tmp_path: Path):
        assert _adopt_a_kept_origenerator(self._manifest(tmp_path)) is None

    def test_adoption_clears_the_channel_but_never_the_status(self, tmp_path: Path):
        """The app rewrites its status file only when a region changes, so one
        cleared here would stay empty while nothing did."""
        status = tmp_path / "origenerator_status.txt"
        status.write_text("ready\n", encoding="utf-8")
        (tmp_path / "origenerator_cmd.txt").write_text("OPEN_SHOWS\n", encoding="utf-8")
        keep_the_origenerator(tmp_path, pid=6060, created_at=44)

        with patch("fun_time.hosted_origenerator.get_process_creation_time",
                   return_value=44):
            _adopt_a_kept_origenerator(self._manifest(tmp_path))

        assert status.read_text(encoding="utf-8") == "ready\n"
        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8") == ""


class TestWhenTheAppWeTookOverNeverAnswers:
    """A takeover is a file left for the open window to read, and a window that
    cannot read it leaves no trace at all.  On 2026-09-28 one was handed to a
    process that had stopped answering: the session reported it hosted, the app
    never published, and the room sat for five minutes with the Origenerator
    mode button grey and nothing anywhere saying why.
    """

    def _bring_up(self, cfg_factory, tmp_path, *, it_publishes):
        origenerator = tmp_path / "origenerator"
        (origenerator / "state").mkdir(parents=True)
        config, manifest = _manifest_for(
            cfg_factory, tmp_path, {"paths": {"origenerator_dir": str(origenerator)}})
        status = Path(manifest.commands.origenerator_status_file)
        launched = {}

        def answer(*_args, **_kw):
            if it_publishes:
                status.parent.mkdir(parents=True, exist_ok=True)
                status.write_text("portrait_item=1\n", encoding="utf-8")

        monitors = [MonitorInfo(0, 0, 2560, 1392), MonitorInfo(2560, 0, 1440, 3440)]
        with patch("fun_time.window_layout.enumerate_monitors", return_value=monitors), \
             patch("fun_time.hosted_origenerator.ANSWER_BUDGET_S", 0.05), \
             patch("fun_time.hosted_origenerator.BOOTING_ANSWER_BUDGET_S", 0.05), \
             patch("fun_time.hosted_origenerator.the_open_origenerator",
                   return_value=OpenOrigenerator(6060, starting=False,
                                                 checkout=origenerator)), \
             patch("fun_time.hosted_origenerator.take_it_over", side_effect=answer), \
             patch("fun_time.hosted_origenerator.launch_origenerator",
                   side_effect=lambda **kw: launched.update(kw) or 91):
            app = bring_up_the_hosted_app(manifest, project_dirs="")
        return config, app, launched

    def test_one_is_launched_instead(self, cfg_factory, tmp_path, caplog):
        _config, app, launched = self._bring_up(
            cfg_factory, tmp_path, it_publishes=False)

        assert app is not None and app.pid == 91 and not app.taken_over
        assert launched, "the room was left with no Origenerator at all"
        assert "published nothing" in caplog.text

    def test_one_that_answers_is_the_app_this_session_hosts(self, cfg_factory, tmp_path):
        _config, app, launched = self._bring_up(
            cfg_factory, tmp_path, it_publishes=True)

        assert not launched, "a second copy was launched beside the one we took over"
        assert app is not None and app.taken_over and app.pid == 6060
