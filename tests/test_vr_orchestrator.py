from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.command_dispatch import BridgeState
from fun_time.config import load_config
from fun_time.shared_state import read_shared_state, write_shared_state
from fun_time_vr.orchestrator import (
    VR_PLAYER_MODULE,
    build_vr_manifest,
    primary_playlist_has_vr,
    resume_vr_state,
    validate_vr_config,
    vr_primary_sources,
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
            "main_monitor": 1, "secondary_monitor": 2,
            "primary_top_ratio": 0.7, "landscape_width_ratio": 0.6
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
        spec = vr_primary_sources(config)
        assert spec.split("|") == [
            str(tmp_path / "library" / "VR" / "finished"),
            str(tmp_path / "library" / "2D"),
        ]

    def test_manifest_overrides_primary_sources_and_adds_the_vr_section(self, config):
        manifest = build_vr_manifest(config)
        assert manifest["media"]["nau_library_sources"] == vr_primary_sources(config)
        vr = manifest["vr"]
        assert vr["player_module"] == VR_PLAYER_MODULE
        assert vr["library_dirs"] == str(config.vr.library_dirs[0])
        assert vr["tcode_udp_host"] == "127.0.0.1"
        assert vr["tcode_udp_port"] == "50557"
        assert vr["audio_device"] == "Example Headset"
        assert vr["compositor_layers"] == "0"

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


class TestResumedPrimaryPlaylist:
    """A desktop session's primary playlist is 2D only; resuming it into a VR
    session would give the headset nothing but flat screens, so the VR session
    checks before honoring the resume."""

    def test_a_desktop_playlist_reads_as_holding_no_vr(self, config, tmp_path):
        playlist = tmp_path / "nau_playlist.tsv"
        playlist.write_text(
            f"{tmp_path / 'library' / '2D' / 'scene one.mp4'}\n"
            f"{tmp_path / 'library' / '2D' / 'scene two.mp4'}\n",
            encoding="utf-8",
        )

        assert primary_playlist_has_vr(playlist, config.vr.library_dirs) is False

    def test_one_vr_entry_is_enough(self, config, tmp_path):
        vr_dir = tmp_path / "library" / "VR" / "finished"
        playlist = tmp_path / "nau_playlist.tsv"
        playlist.write_text(
            f"{tmp_path / 'library' / '2D' / 'scene one.mp4'}\n"
            f"{vr_dir / 'scene three.mp4'}\t{tmp_path / 'scene three.funscript'}\n",
            encoding="utf-8",
        )

        assert primary_playlist_has_vr(playlist, config.vr.library_dirs) is True

    def test_a_missing_playlist_reads_as_holding_no_vr(self, config, tmp_path):
        assert primary_playlist_has_vr(tmp_path / "absent.tsv", config.vr.library_dirs) is False


class TestResumeVrState:
    """The desktop session shares this state dir, and it can carry a primary
    mode a VR session has no player for."""

    def test_a_genau_mode_left_by_the_desktop_session_does_not_come_across(self, tmp_path):
        """Genau is not launched in VR, so a carried genau mode would leave every
        HUD naming a player that is not running — and the state file is what all
        of them read, so it has to be corrected on disk, not just in hand."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(primary_mode="genau", volume=40))

        carried = resume_vr_state(state_file, resumed=True)

        assert carried.primary_mode == "nau"
        assert read_shared_state(state_file).primary_mode == "nau"

    def test_everything_else_the_desktop_session_left_still_comes_across(self, tmp_path):
        """Both apps run the same players for these, off the same playlists —
        only the primary slot's second seat is missing here."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(
            primary_mode="genau", volume=40, portrait_f_mode=True, locked3=True,
        ))

        carried = resume_vr_state(state_file, resumed=True)

        assert (carried.volume, carried.portrait_f_mode, carried.locked3) == (40, True, True)
