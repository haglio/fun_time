from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from fun_time.shared_state import BridgeState
from fun_time.config import load_config
from fun_time.shared_state import read_shared_state, write_shared_state
from fun_time_vr.player import VrSettings
from fun_time_vr.orchestrator import (
    VR_PLAYER_MODULE,
    build_vr_manifest,
    stock_the_playlists,
    main_playlist_has_vr,
    resume_vr_state,
    validate_vr_config,
    vr_main_sources,
)


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

        assert written - {"player_module"} == read_back

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


class TestResumeVrState:
    """The desktop session shares this state dir, and it can carry a main
    mode a VR session has no player for."""

    def test_a_genau_mode_left_by_the_desktop_session_does_not_come_across(self, tmp_path):
        """Genau is not launched in VR, so a carried genau mode would leave every
        HUD naming a player that is not running — and the state file is what all
        of them read, so it has to be corrected on disk, not just in hand."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_mode="genau", volume=40))

        carried = resume_vr_state(state_file, resumed=True)

        assert carried.main_mode == "nau"
        assert read_shared_state(state_file).main_mode == "nau"

    def test_everything_else_the_desktop_session_left_still_comes_across(self, tmp_path):
        """Both apps run the same players for these, off the same playlists —
        only the main slot's second seat is missing here."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(
            main_mode="genau", volume=40, portrait_f_mode=True, locked3=True,
        ))

        carried = resume_vr_state(state_file, resumed=True)

        assert (carried.volume, carried.portrait_f_mode, carried.locked3) == (40, True, True)


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
             patch("fun_time.single_instance.try_acquire_mutex", return_value=object()), \
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
             patch("fun_time.single_instance.try_acquire_mutex", return_value=object()), \
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

