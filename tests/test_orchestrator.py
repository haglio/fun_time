"""Tests for fun_time.orchestrator."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fun_time.orchestrator import (
    build_controller_args,
    build_parser,
    require_dir,
    require_file,
    validate_config,
)
from fun_time.config import load_config


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------

class TestBuildParser:
    def test_defaults(self):
        args = build_parser().parse_args([])
        assert args.config is None
        assert args.check is False

    def test_check_flag(self):
        args = build_parser().parse_args(["--check"])
        assert args.check is True

    def test_config_argument(self):
        args = build_parser().parse_args(["--config", "/path/config.json"])
        assert args.config == "/path/config.json"


# ---------------------------------------------------------------------------
# require_file / require_dir
# ---------------------------------------------------------------------------

class TestRequireFile:
    def test_passes_for_existing_file(self, tmp_path: Path):
        f = tmp_path / "exists.txt"
        f.touch()
        require_file(f)  # should not raise

    def test_raises_for_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Missing file"):
            require_file(tmp_path / "missing.exe")

    def test_raises_for_directory(self, tmp_path: Path):
        # A directory is not a file
        with pytest.raises(FileNotFoundError):
            require_file(tmp_path)


class TestRequireDir:
    def test_passes_for_existing_dir(self, tmp_path: Path):
        d = tmp_path / "mydir"
        d.mkdir()
        require_dir(d)  # should not raise

    def test_raises_for_missing_dir(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Missing directory"):
            require_dir(tmp_path / "nope")

    def test_raises_for_file(self, tmp_path: Path):
        f = tmp_path / "notadir.txt"
        f.touch()
        with pytest.raises(FileNotFoundError):
            require_dir(f)


# ---------------------------------------------------------------------------
# build_controller_args
# ---------------------------------------------------------------------------

class TestBuildControllerArgs:
    def test_returns_list(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_args(cfg, "testpass")
        assert isinstance(result, list)

    def test_vlc_pass_is_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_args(cfg, "mysecret")
        assert "mysecret" in result

    def test_vlc_ports_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_args(cfg, "pw")
        assert "8091" in result
        assert "8092" in result

    def test_config_path_at_end(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_args(cfg, "pw")
        assert result[-1] == str(cfg.config_path)

    def test_primary_vlc_dirs_joined_with_pipe(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "extra_vlc"
        extra.mkdir()
        path = cfg_factory({"paths": {"primary_vlc_dirs": [
            str(tmp_path / "vlc_primary"),
            str(extra),
        ]}})
        cfg = load_config(path)
        args = build_controller_args(cfg, "pw")
        pipe_joined = [a for a in args if "|" in a]
        assert len(pipe_joined) == 1
        assert str(tmp_path / "vlc_primary") in pipe_joined[0]
        assert str(extra) in pipe_joined[0]

    def test_robot_hand_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_args(cfg, "pw")
        assert "fun_time.robot_hand.app" in result

    def test_audio_companion_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_args(cfg, "pw")
        assert "fun_time.audio_companion_app" in result


# ---------------------------------------------------------------------------
# validate_config – only tests logic that doesn't require real binaries
# ---------------------------------------------------------------------------

class TestValidateConfig:
    def _make_config_with_stubs(self, cfg_path: Path, tmp_path: Path):
        """Load config and create all stub files validate_config needs."""
        cfg = load_config(cfg_path)
        # Create stub executable files
        for p in (cfg.paths.vlc_exe, cfg.paths.mfp_exe, cfg.paths.ahk_exe, cfg.paths.python_exe):
            p.touch()
        # Create AHK script
        (cfg.project_dir / "controller.ahk").touch()
        # Create Python entry points
        broker_py = cfg.project_dir / "fun_time" / "broker_app.py"
        broker_py.parent.mkdir(parents=True, exist_ok=True)
        broker_py.touch()
        rh_py = cfg.project_dir / "fun_time" / "robot_hand" / "app.py"
        rh_py.parent.mkdir(parents=True, exist_ok=True)
        rh_py.touch()
        ac_py = cfg.project_dir / "fun_time" / "audio_companion_app.py"
        ac_py.touch()
        return cfg

    def test_raises_when_vlc_exe_missing(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        # Do NOT create vlc_exe stub
        with pytest.raises(FileNotFoundError):
            validate_config(cfg)
