from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.shared_state import BridgeState, read_shared_state, write_shared_state
from fun_time_vr.genau_settings import GenauSettings
from fun_time_vr.orchestrator import (
    VR_PLAYER_MODULE,
    _release_vr_runtime,
    build_vr_manifest,
    is_vr_pin,
    main_playlist_has_vr,
    stamp_vr_shortcut_aumid,
    stock_the_playlists,
    validate_vr_config,
    vr_main_sources,
)
from fun_time_vr.player import VrSettings


@pytest.fixture
def config(tmp_path, monkeypatch):
    """A loadable config with a VR section, all paths fabricated under tmp."""
    library = tmp_path / "library"
    vr_dir = library / "VR" / "finished"
    flat_dir = library / "2D"
    for directory in (vr_dir, flat_dir, tmp_path / "clips", tmp_path / "audio", tmp_path / "weird"):
        directory.mkdir(parents=True)
    ahk = tmp_path / "AutoHotkey64.exe"
    python = tmp_path / "python.exe"
    ahk.write_bytes(b"")
    python.write_bytes(b"")
    config_path = tmp_path / "fun_time_config.json"
    config_path.write_text(
        """
        {
          "paths": {
            "ahk_exe": "%(ahk)s",
            "python_exe": "%(python)s",
            "nau_library_dirs": ["%(flat)s"],
            "portrait_dirs": ["%(flat)s"],
            "landscape_dirs": ["%(flat)s"],
            "weird_dir": "%(weird)s",
            "clips_dir": "%(clips)s",
            "audio_dir": "%(audio)s",
            "favs_file": "%(favs)s",
            "state_dir": "%(state)s"
          },
          "layout": {
            "primary_monitor": 1, "secondary_monitor": 2,
            "main_top_ratio": 0.7, "landscape_width_ratio": 0.6
          },
          "audio_companion": {"host": "127.0.0.1", "port": 50556},
          "vr": {
            "library_dirs": ["%(vr)s"],
            "audio_device": "Example Headset",
            "tcode_udp_port": 50557
          }
        }
        """
        % {
            "ahk": str(ahk).replace("\\", "/"),
            "python": str(python).replace("\\", "/"),
            "flat": str(flat_dir).replace("\\", "/"),
            "weird": str(tmp_path / "weird").replace("\\", "/"),
            "clips": str(tmp_path / "clips").replace("\\", "/"),
            "audio": str(tmp_path / "audio").replace("\\", "/"),
            "favs": str(tmp_path / "favs.csv").replace("\\", "/"),
            "state": str(tmp_path / "state").replace("\\", "/"),
            "vr": str(vr_dir).replace("\\", "/"),
        },
        encoding="utf-8",
    )
    return load_config(config_path)


class TestVrConfig:
    def test_vr_section_loads(self, config, tmp_path):
        assert config.vr.library_dirs == (tmp_path / "library" / "VR" / "finished",)
        assert config.vr.audio_device == "Example Headset"
        assert config.vr.tcode_udp_host == "127.0.0.1"
        assert config.vr.tcode_udp_port == 50557
        # Off by default: the bundled Pimax runtime accepts quad layers and
        # never composites them (screens submitted that way don't appear).
        assert config.vr.compositor_layers is False

    def test_absent_vr_section_defaults_empty(self, config, tmp_path):
        raw = (tmp_path / "fun_time_config.json").read_text(encoding="utf-8")
        stripped = raw[: raw.rindex(',\n          "vr"')] + "\n        }"
        bare_path = tmp_path / "bare_config.json"
        bare_path.write_text(stripped, encoding="utf-8")
        bare = load_config(bare_path)
        assert bare.vr.library_dirs == ()
        assert bare.vr.audio_device is None

    def test_validate_rejects_a_missing_vr_dir(self, config, tmp_path):
        (tmp_path / "library" / "VR" / "finished").rmdir()
        with pytest.raises(FileNotFoundError):
            validate_vr_config(config)


class TestVrManifest:
    def test_primary_sources_merge_vr_first_then_the_desktop_dirs(self, config, tmp_path):
        spec = vr_main_sources(config)
        assert spec.split("|") == [
            str(tmp_path / "library" / "VR" / "finished"),
            str(tmp_path / "library" / "2D"),
        ]

    def test_manifest_overrides_primary_sources_and_adds_the_vr_section(self, config):
        manifest = build_vr_manifest(config)
        assert manifest["media"]["nau_library_sources"] == vr_main_sources(config)
        vr = manifest["vr"]
        assert vr["player_module"] == VR_PLAYER_MODULE
        assert vr["library_dirs"] == str(config.vr.library_dirs[0])
        assert vr["tcode_udp_host"] == "127.0.0.1"
        assert vr["tcode_udp_port"] == "50557"
        assert vr["audio_device"] == "Example Headset"
        assert vr["compositor_layers"] == "0"

    def test_every_vr_key_the_writer_emits_has_a_field_to_land_in(self, config):
        """The ``[vr]`` writer and ``VrSettings`` are two halves of one schema.

        ``fun_time.manifest`` pins this for the sections it owns and passes over
        this one on purpose, so nothing checked that the two ends of the section
        FunTimeVR writes to itself still agreed — and they had already stopped:
        ``audio_device`` was written every launch and had no field to be read
        into, which is how the VR main player lost its audio-device routing.
        ``player_module`` is the one key with no reader by design: the launcher
        starts the module it names from its own constant.
        """
        written = set(build_vr_manifest(config)["vr"])
        read_back = {field.name for field in fields(VrSettings)}
        # Genau's numbers ride flat and land together, in the one `genau` field.
        genau_keys = set(GenauSettings().manifest_fields())

        assert written - {"player_module"} - genau_keys == read_back - {"genau"}
        assert genau_keys <= written

    def test_manifest_carries_a_layers_opt_in(self, config):
        import dataclasses  # noqa: PLC0415

        opted_in = dataclasses.replace(
            config, vr=dataclasses.replace(config.vr, compositor_layers=True)
        )
        assert build_vr_manifest(opted_in)["vr"]["compositor_layers"] == "1"

    def test_everything_else_is_the_desktop_manifest(self, config):
        manifest = build_vr_manifest(config)
        assert manifest["modules"]["satellite_module"] == "satellite"
        assert Path(manifest["commands"]["nau_cmd_file"]).name == "nau_cmd.txt"


class TestNoOrigeneratorInVr:
    """A VR session hosts no Origenerator, and its manifest has to say so.

    ``run_vr_bridge`` launches the audio companion and the VR player and
    nothing else, so the checkout the desktop manifest names is a mode with no
    app to run it.  Left in, the satellites' HUDs drew the Origenerator/Video
    pair and a session resumed out of a desktop session's origenerator mode
    opened in it, both sides labeled "Origenerator mode" over players the
    hosted app was never started to cover.
    """

    @pytest.fixture
    def hosted(self, config, tmp_path):
        """The same config with an Origenerator named, as a real one names it."""
        from dataclasses import replace

        origenerator = tmp_path / "origenerator"
        origenerator.mkdir()
        return replace(config, paths=replace(
            config.paths,
            origenerator_dir=origenerator,
            origenerator_python_exe=tmp_path / "system_python.exe",
        ))

    def test_the_desktop_manifest_does_name_one(self, hosted, tmp_path):
        """The precondition, so the assertions below cannot pass vacuously."""
        from fun_time.manifest import build_windows_bridge_manifest

        desktop = build_windows_bridge_manifest(hosted)
        assert desktop["runtime"]["origenerator_dir"] == str(tmp_path / "origenerator")
        assert desktop["executables"]["origenerator_python_exe"]

    def test_the_vr_manifest_names_none(self, hosted):
        manifest = build_vr_manifest(hosted)

        assert manifest["runtime"]["origenerator_dir"] == ""
        assert manifest["executables"]["origenerator_python_exe"] == ""

    def test_the_session_reads_back_as_hosting_none(self, hosted, tmp_path):
        """The join the rest hangs off: ``origenerator_enabled`` is what keeps
        the mode pair off both HUDs, pulls a resumed origenerator mode back to
        video, and answers the switch with a notice instead of a dead end."""
        from fun_time.manifest import LaunchManifest, write_manifest_data
        from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest

        path = write_manifest_data(build_vr_manifest(hosted), tmp_path / "launch.ini")
        bridge = build_bridge_config_from_manifest(
            LaunchManifest.read(path), vr_main_player=True)

        assert bridge.origenerator_enabled is False


class _FakeProc:
    """poll()/terminate()/wait() shaped like subprocess.Popen, exiting after a
    set number of polls."""

    def __init__(self, exits_after_polls=None):
        self._exits_after = exits_after_polls
        self._polls = 0
        self.terminated = False

    def poll(self):
        if self._exits_after is None:
            return None
        self._polls += 1
        return 0 if self._polls > self._exits_after else None

    def terminate(self):
        self.terminated = True
        self._exits_after = 0

    def wait(self):
        return 0


class TestWaitForSessionEnd:
    def test_ahk_exit_ends_the_session(self):
        from fun_time_vr.orchestrator import _wait_for_session_end

        assert _wait_for_session_end(
            _FakeProc(exits_after_polls=2), _FakeProc(), poll_s=0.0
        ) == "ahk"

    def test_player_exit_ends_the_session_too(self):
        # The VR player's window is the session's only window, so closing it
        # must end the whole session — an orchestrator that kept waiting on
        # AHK held the single-instance mutex and blocked every relaunch.
        from fun_time_vr.orchestrator import _wait_for_session_end

        assert _wait_for_session_end(
            _FakeProc(), _FakeProc(exits_after_polls=2), poll_s=0.0
        ) == "player"


class TestResumedMainPlaylist:
    """A desktop session's main playlist is 2D only; resuming it into a VR
    session would give the headset nothing but flat screens, so the VR session
    checks before honoring the resume."""

    def test_a_desktop_playlist_reads_as_holding_no_vr(self, config, tmp_path):
        playlist = tmp_path / "nau_playlist.tsv"
        playlist.write_text(
            f"{tmp_path / 'library' / '2D' / 'scene one.mp4'}\n"
            f"{tmp_path / 'library' / '2D' / 'scene two.mp4'}\n",
            encoding="utf-8",
        )

        assert main_playlist_has_vr(playlist, config.vr.library_dirs) is False

    def test_one_vr_entry_is_enough(self, config, tmp_path):
        vr_dir = tmp_path / "library" / "VR" / "finished"
        playlist = tmp_path / "nau_playlist.tsv"
        playlist.write_text(
            f"{tmp_path / 'library' / '2D' / 'scene one.mp4'}\n"
            f"{vr_dir / 'scene three.mp4'}\t{tmp_path / 'scene three.funscript'}\n",
            encoding="utf-8",
        )

        assert main_playlist_has_vr(playlist, config.vr.library_dirs) is True

    def test_a_missing_playlist_reads_as_holding_no_vr(self, config, tmp_path):
        assert main_playlist_has_vr(tmp_path / "absent.tsv", config.vr.library_dirs) is False


class TestTheModeASessionComesBackIn:
    """The desktop session shares this state dir, and the VR player hosts both
    of the main slot's players now -- so the mode it was closed in comes across
    with everything else, and the session is seeded and revealed in it."""

    @staticmethod
    def _calls(function_name: str, spelling: str):
        import ast
        import inspect

        from fun_time_vr import orchestrator

        tree = ast.parse(inspect.getsource(getattr(orchestrator, function_name)))
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and ast.unparse(n.func) == spelling]

    def test_the_state_is_no_longer_corrected_on_the_way_in(self, tmp_path):
        """There used to be a resume_vr_state that struck the mode out; the state
        the session opens on is fun_time's own resume, mode and all."""
        from fun_time_vr import orchestrator

        assert not hasattr(orchestrator, "resume_vr_state")
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_mode="genau", volume=40))

        assert read_shared_state(state_file).main_mode == "genau"

    def test_the_flags_are_seeded_in_the_carried_mode(self):
        """Seeded in the default instead, Genau's role would open as the HUD layer
        under a video the dispatch loop believes is parked."""
        import ast

        (seed,) = self._calls("run_vr_bridge", "seed_startup_states")
        given = {kw.arg: ast.unparse(kw.value) for kw in seed.keywords}

        assert given["mode"] == "carried.main_mode"

    def test_the_reveal_releases_the_players_the_mode_puts_to_work(self):
        """The desktop's own reveal: the video in video mode, Genau's hand and its
        music in genau mode -- rather than unpausing the video whatever the mode."""
        import ast

        (release,) = self._calls("run_vr_bridge", "release_the_players")

        assert [ast.unparse(arg) for arg in release.args] == ["manifest", "carried.main_mode"]
        assert self._calls("run_vr_bridge", "write_flag_file") == []


class TestLaunchVrPlayer:
    def test_the_player_starts_on_the_named_python_against_the_manifest(self, tmp_path):
        """The command line is the whole contract: OUR interpreter (the VR
        player ships from this repo), the player module, and the manifest that
        tells it everything else — with its console kept in a log, because
        under pythonw an import-time death is otherwise traceless."""
        from unittest.mock import patch

        from fun_time_vr.orchestrator import launch_vr_player

        with patch("fun_time_vr.orchestrator.subprocess.Popen") as popen:
            launch_vr_player(
                python_exe=tmp_path / "python.exe",
                manifest_path=tmp_path / "windows_bridge_launch.ini",
                log_file=tmp_path / "vr_player.log",
            )

        command = popen.call_args[0][0]
        assert command == [
            str(tmp_path / "python.exe"), "-m", VR_PLAYER_MODULE,
            "--manifest", str(tmp_path / "windows_bridge_launch.ini"),
        ]
        assert popen.call_args.kwargs["stdout"] is popen.call_args.kwargs["stderr"]
        assert (tmp_path / "vr_player.log").exists()


class TestWaitForPlayer:
    """The startup readiness handshake: the first status write means ready,
    and both ways it can fail are reported at once, by name."""

    class _Alive:
        returncode = None

        def poll(self):
            return None

    class _Dead:
        returncode = 3

        def poll(self):
            return 3

    def test_the_first_status_write_is_ready(self, tmp_path):
        from fun_time_vr.orchestrator import _wait_for_player

        status = tmp_path / "nau_status.txt"
        status.write_text("video=C:\\v\\scene one.mp4\n", encoding="utf-8")

        assert _wait_for_player(status, self._Alive()) is True

    def test_an_early_death_is_reported_at_once_not_after_the_timeout(self, tmp_path, caplog):
        import logging

        from fun_time_vr.orchestrator import _wait_for_player

        with caplog.at_level(logging.ERROR, logger="fun_time_vr.orchestrator"):
            ready = _wait_for_player(tmp_path / "nau_status.txt", self._Dead())

        assert ready is False
        assert "exited during startup" in caplog.text
        assert "3" in caplog.text

    def test_a_silent_player_is_given_up_on_at_the_deadline(self, tmp_path, caplog, monkeypatch):
        import logging

        from fun_time_vr import orchestrator

        monkeypatch.setattr(orchestrator, "PLAYER_READY_TIMEOUT_S", 0.0)
        with caplog.at_level(logging.ERROR, logger="fun_time_vr.orchestrator"):
            ready = orchestrator._wait_for_player(tmp_path / "nau_status.txt", self._Alive())

        assert ready is False
        assert "published no status" in caplog.text


class TestTheCheckRun:
    """``--check`` validates the config and stops, which is what `launch_vr.vbs`
    uses to refuse a bad session before any window is opened."""

    def test_a_valid_config_checks_out(self, config, monkeypatch, tmp_path):
        from unittest.mock import patch

        from fun_time_vr import orchestrator

        with patch.object(orchestrator, "load_config", return_value=config), \
             patch("app_support.win32.try_acquire_mutex", return_value=object()), \
             patch.object(orchestrator, "install_exception_logging"):
            assert orchestrator.main(["--check"]) == 0

    def test_the_handlers_land_on_the_logger_this_module_writes_through(self, config):
        """Every function here logs through the module-level ``logger``, so the
        one ``main`` sets up has to BE that one — it used to be threaded back in
        as a parameter under a second name, which was the same object only
        because the string happened to match."""
        import logging
        from unittest.mock import patch

        from fun_time_vr import orchestrator

        configured: list[logging.Logger] = []
        with patch.object(orchestrator, "load_config", return_value=config), \
             patch("app_support.win32.try_acquire_mutex", return_value=object()), \
             patch.object(orchestrator, "install_exception_logging"), \
             patch.object(orchestrator, "configure_logging",
                          side_effect=lambda name, *_a, **_k: (
                              configured.append(logging.getLogger(name)),
                              logging.getLogger(name))[1]):
            orchestrator.main(["--check"])

        assert configured == [orchestrator.logger]

    def test_and_it_asks_for_the_name_this_module_logs_under(self):
        """Under pytest every spelling coincides, so only the source can say
        which was written.  They do NOT coincide in the launch:
        `launch_vr.vbs` runs `python -m fun_time_vr.orchestrator`, where
        `__name__` is `"__main__"` — so a literal configured a logger this
        module never wrote through, and `__name__` would put "__main__" in
        every line of the log."""
        import ast
        import inspect

        from fun_time_vr import orchestrator

        assert orchestrator.logger.name == "fun_time_vr.orchestrator"

        tree = ast.parse(inspect.getsource(orchestrator.main))
        call = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "configure_logging")

        # `logger.name`: whatever this module logs under, that is what is set up.
        assert isinstance(call.args[0], ast.Attribute)
        assert call.args[0].attr == "name"
        assert call.args[0].value.id == "logger"


class TestStockingThePlaylists:
    """The three playlists a VR session opens on, built through the real
    fun_time.modes builder — so a change to its signature is caught here rather
    than by a launch that writes vr_launcher.ready and then dies (2026-09-03:
    build_all_playlists grew SatelliteBuild arguments, the desktop caller moved
    with it, this one did not, and FunTimeVR could not start at all)."""

    def _manifest(self, config, tmp_path):
        from fun_time.manifest import LaunchManifest, write_manifest_data

        return LaunchManifest.read(
            write_manifest_data(build_vr_manifest(config), tmp_path / "launch.ini")
        )

    def test_a_fresh_session_gets_all_three(self, config, tmp_path):
        library = tmp_path / "library"
        (library / "VR" / "finished" / "scene one.mp4").write_bytes(b"")
        (library / "2D" / "scene two.mp4").write_bytes(b"")
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)

        stock_the_playlists(
            self._manifest(config, tmp_path),
            state_dir=state,
            metadata_root=tmp_path / "metadata",
            vr_library_dirs=config.vr.library_dirs,
            resumed=False,
            main_f_mode=False,
            main_recent=False,
        )

        written = sorted(p.name for p in state.glob("*playlist*.tsv"))
        assert len(written) == 3, written


def test_a_session_puts_back_down_the_vr_runtime_it_brought_up(monkeypatch):
    """Started hidden, so nothing on screen would offer to quit it afterwards."""
    calls = []
    monkeypatch.setattr(
        "fun_time_vr.orchestrator.vr_runtime.stop_runtime", lambda: calls.append("stop")
    )
    _release_vr_runtime(was_up=False)
    assert calls == ["stop"]


def test_a_session_leaves_a_vr_runtime_that_was_already_the_users(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "fun_time_vr.orchestrator.vr_runtime.stop_runtime", lambda: calls.append("stop")
    )
    _release_vr_runtime(was_up=True)
    assert calls == []


class TestTheVrClipsFolder:
    """Genau's engine browses a folder of VR180 masters in the headset, where
    the desktop's clips folder holds flat ones."""

    def test_it_is_read_from_the_vr_section(self, config, tmp_path):
        import json

        raw = json.loads((tmp_path / "fun_time_config.json").read_text(encoding="utf-8"))
        raw["vr"]["clips_dir"] = str(tmp_path / "vr_clips").replace("\\", "/")
        named = tmp_path / "named_config.json"
        named.write_text(json.dumps(raw), encoding="utf-8")

        assert load_config(named).vr.clips_dir == tmp_path / "vr_clips"

    def test_unset_it_is_none_and_the_desktops_clips_are_browsed_alone(self, config):
        assert config.vr.clips_dir is None


class TestGenausRoleInTheManifest:
    """What the VR player needs to run Genau's engine, carried in the one file
    it reads: the folder, the companion's address, and the engine's numbers."""

    def test_the_vr_clips_folder_rides_when_named(self, config, tmp_path):
        from dataclasses import replace

        named = replace(config, vr=replace(config.vr, clips_dir=tmp_path / "vr_clips"))

        vr = build_vr_manifest(named)["vr"]
        assert vr["clips_dirs"] == f"{tmp_path / 'vr_clips'}|{config.paths.clips_dir}"
        assert vr["vr_clip_dirs"] == str(tmp_path / "vr_clips")

    def test_the_desktop_clips_are_browsed_alone_when_no_vr_folder_is_named(self, config):
        vr = build_vr_manifest(config)["vr"]

        assert vr["clips_dirs"] == str(config.paths.clips_dir)
        assert vr["vr_clip_dirs"] == ""

    def test_the_companions_address_is_fun_times_own(self, config):
        vr = build_vr_manifest(config)["vr"]

        assert (vr["notify_host"], vr["notify_port"]) == ("127.0.0.1", "50556")

    def test_genaus_numbers_come_off_genaus_config(self, config, tmp_path):
        import json
        from dataclasses import replace

        genau_config = tmp_path / "genau_config.json"
        genau_config.write_text(json.dumps({"genau": {"beats_per_loop": 2.0, "udp_port": 50999}}),
                                encoding="utf-8")
        pointed = replace(config, paths=replace(config.paths, genau_config_path=genau_config))

        vr = build_vr_manifest(pointed)["vr"]

        assert vr["beats_per_loop"] == "2.0"
        assert vr["beat_udp_port"] == "50999"
        assert vr["bpm_smoothing"] == str(GenauSettings().bpm_smoothing)

    def test_the_player_reads_all_of_it_back(self, config, tmp_path):
        from fun_time.manifest import write_manifest_data

        path = write_manifest_data(build_vr_manifest(config), tmp_path / "manifest.ini")

        settings = VrSettings.read(path)

        assert settings.clips_dirs == (config.paths.clips_dir,)
        assert settings.vr_clip_dirs == ()
        assert (settings.notify_host, settings.notify_port) == ("127.0.0.1", 50556)
        assert settings.genau == GenauSettings()


class TestTheVrPin:
    """The VR session's pinned button is its own, lit by the VR player's window."""

    @pytest.mark.parametrize("stem", ["Fun Time VR", "fun time vr", "Fun Time VR (2)"])
    def test_the_vr_shortcut_and_its_copies_are_ours(self, stem):
        assert is_vr_pin(stem) is True

    @pytest.mark.parametrize("stem", ["Fun Time", "Fun Time (2)", "GenauVR", "Genau"])
    def test_the_desktop_pin_and_every_other_are_not(self, stem):
        assert is_vr_pin(stem) is False

    def test_only_the_vr_pins_are_stamped_with_the_vr_identity(self, tmp_path):
        from unittest.mock import patch

        from fun_time.win32_taskbar import VR_APP_USER_MODEL_ID

        for name in ("Fun Time.lnk", "Fun Time VR.lnk", "Fun Time VR (2).lnk", "Other.lnk"):
            (tmp_path / name).write_bytes(b"")
        with patch("fun_time_vr.orchestrator.taskbar_pin_dir", return_value=tmp_path), \
             patch("fun_time_vr.orchestrator.set_shortcut_app_user_model_id") as stamp:
            stamp_vr_shortcut_aumid()

        assert sorted(call.args for call in stamp.call_args_list) == [
            (str(tmp_path / "Fun Time VR (2).lnk"), VR_APP_USER_MODEL_ID),
            (str(tmp_path / "Fun Time VR.lnk"), VR_APP_USER_MODEL_ID),
        ]

    def test_a_pin_that_will_not_take_the_stamp_is_logged_and_left(self, tmp_path):
        from unittest.mock import MagicMock, patch

        (tmp_path / "Fun Time VR.lnk").write_bytes(b"")
        log = MagicMock()
        with patch("fun_time_vr.orchestrator.taskbar_pin_dir", return_value=tmp_path), \
             patch("fun_time_vr.orchestrator.set_shortcut_app_user_model_id",
                   side_effect=OSError("locked")), \
             patch("fun_time_vr.orchestrator.logger", log):
            stamp_vr_shortcut_aumid()   # must not raise

        assert "Could not stamp" in log.warning.call_args.args[0]

    def test_a_session_on_another_config_leaves_the_pin_alone(self, config):
        from unittest.mock import patch

        from fun_time_vr import orchestrator

        with patch.object(orchestrator, "load_config", return_value=config), \
             patch("app_support.win32.try_acquire_mutex", return_value=object()), \
             patch.object(orchestrator, "install_exception_logging"), \
             patch.object(orchestrator, "stamp_vr_shortcut_aumid") as stamp:
            orchestrator.main(["--check"])

        stamp.assert_not_called()

    def test_the_installed_app_stamps_its_own_pin(self, config):
        from unittest.mock import patch

        from fun_time_vr import orchestrator

        with patch.object(orchestrator, "load_config", return_value=config), \
             patch.object(orchestrator, "DEFAULT_CONFIG_PATH", config.config_path), \
             patch("app_support.win32.try_acquire_mutex", return_value=object()), \
             patch.object(orchestrator, "install_exception_logging"), \
             patch.object(orchestrator, "stamp_vr_shortcut_aumid") as stamp:
            orchestrator.main(["--check"])

        stamp.assert_called_once_with()


class TestTheAudioCompanionInVr:
    """Launched as on the desktop, before the player so it is listening when
    Genau's role says which clip is up, and sent to the headset's output."""

    def test_it_is_launched_on_the_headsets_output(self):
        import ast
        import inspect

        from fun_time_vr import orchestrator

        tree = ast.parse(inspect.getsource(orchestrator.run_vr_bridge))
        (launch,) = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and ast.unparse(n.func) == "launch_audio_companion"]
        given = {kw.arg: ast.unparse(kw.value) for kw in launch.keywords}

        assert given["audio_device"] == "config.vr.audio_device"
        assert given["audio_folder"] == "manifest.media.genau_audio"

    def test_it_is_launched_before_the_player_and_killed_with_it(self):
        import ast
        import inspect

        from fun_time_vr import orchestrator

        source = inspect.getsource(orchestrator.run_vr_bridge)
        tree = ast.parse(source)
        calls = {ast.unparse(n.func): n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)}

        assert calls["launch_audio_companion"] < calls["launch_vr_player"]
        assert "for child in children.values():" in source
