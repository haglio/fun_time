from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time.command_dispatch import (
    BridgeState,
    BridgeConfig,
    WindowOp,
    dispatch_command,
)


def _make_config(tmp_path: Path) -> BridgeConfig:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text("local_file,web_url\n", encoding="utf-8")
    weird_dir = tmp_path / "weird"
    weird_dir.mkdir(exist_ok=True)
    return BridgeConfig(
        primary_port=8090,
        portrait_port=8091,
        landscape_port=8092,
        vlc_password="pw",
        favs_file=favs_file,
        weird_dir=weird_dir,
        state_dir=state_dir,
        primary_sources=str(tmp_path / "primary"),
        portrait_sources=str(tmp_path / "portrait"),
        landscape_sources=str(tmp_path / "landscape"),
        genau_mode_file=state_dir / "genau_mode.txt",
        genau_cmd_file=state_dir / "genau_cmd.txt",
        genau_paused_file=state_dir / "genau_paused.txt",
        audio_paused_file=state_dir / "audio_paused.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
    )


def _make_state(**overrides) -> BridgeState:
    defaults = dict(
        locked2=False,
        locked3=False,
        genau_mode=False,
        f_mode_enabled=False,
        omni_paused=False,
    )
    defaults.update(overrides)
    return BridgeState(**defaults)


# --- portrait_lock ---


def test_portrait_lock_toggles_lock_on(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value="C:\\clips\\portrait.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.ensure_in_favs"),
        patch("fun_time.command_dispatch.vlc_http_cmd", return_value=True),
    ):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is True
    assert new_state.locked3 is False


def test_portrait_lock_toggles_lock_off(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value="C:\\clips\\portrait.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.vlc_http_cmd", return_value=True),
    ):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is False


# --- landscape_lock ---


def test_landscape_lock_toggles_lock_on(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=False)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value="C:\\clips\\landscape.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.ensure_in_favs"),
        patch("fun_time.command_dispatch.vlc_http_cmd", return_value=True),
    ):
        new_state, ops = dispatch_command("landscape_lock", state, config)

    assert new_state.locked3 is True


# --- portrait_trash ---


def test_portrait_trash_unlocks_and_discards(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value="C:\\clips\\portrait.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.remove_from_favs"),
        patch("fun_time.command_dispatch.move_to_weird"),
        patch("fun_time.command_dispatch.vlc_advance_and_remove", return_value=True),
    ):
        new_state, ops = dispatch_command("portrait_trash", state, config)

    assert new_state.locked2 is False


def test_portrait_trash_ensures_playback_after_discard(tmp_path: Path):
    """After discarding, VLC must be confirmed playing to prevent black screen."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    playback_calls: list[tuple[int, str, bool]] = []

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value="C:\\clips\\portrait.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.remove_from_favs"),
        patch("fun_time.command_dispatch.move_to_weird"),
        patch("fun_time.command_dispatch.vlc_advance_and_remove", return_value=True),
        patch("fun_time.command_dispatch.ensure_playback_state",
              side_effect=lambda p, pw, should_play: playback_calls.append((p, pw, should_play)) or True),
    ):
        dispatch_command("portrait_trash", state, config)

    assert (config.portrait_port, config.vlc_password, True) in playback_calls


def test_portrait_trash_uses_advance_and_remove_not_pl_next(tmp_path: Path):
    """Discard must use vlc_advance_and_remove (ID-based advance + playlist
    cleanup) instead of pl_next, which is unreliable after manual navigation."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    advance_calls: list[tuple] = []
    http_cmds: list[str] = []

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value="C:\\clips\\portrait.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.remove_from_favs"),
        patch("fun_time.command_dispatch.move_to_weird"),
        patch("fun_time.command_dispatch.vlc_advance_and_remove",
              side_effect=lambda p, pw: advance_calls.append((p, pw)) or True),
        patch("fun_time.command_dispatch.vlc_http_cmd",
              side_effect=lambda p, cmd, pw: http_cmds.append(cmd) or True),
    ):
        dispatch_command("portrait_trash", state, config)

    assert advance_calls == [(8091, "pw")], "discard must call vlc_advance_and_remove"
    assert "pl_next" not in http_cmds, "discard must not use pl_next"


# --- portrait_prev / portrait_next ---


def test_portrait_prev_cancels_lock_and_calls_nav_step_prev(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step", side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        new_state, ops = dispatch_command("portrait_prev", state, config)

    assert new_state.locked2 is False
    assert nav_calls == [(8091, "prev")]


def test_portrait_next_calls_nav_step_next(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step", side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        dispatch_command("portrait_next", state, config)

    assert nav_calls == [(8091, "next")]


def test_portrait_prev_ensures_playback_after_nav(tmp_path: Path):
    """Navigation must ensure VLC is actually playing after pl_play, to prevent
    black screen / stopped state on item transitions."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    playback_calls: list[tuple[int, str, bool]] = []

    with (
        patch("fun_time.command_dispatch.vlc_nav_step", return_value=True),
        patch("fun_time.command_dispatch.ensure_playback_state",
              side_effect=lambda p, pw, should_play: playback_calls.append((p, pw, should_play)) or True),
    ):
        dispatch_command("portrait_prev", state, config)

    assert (config.portrait_port, config.vlc_password, True) in playback_calls


def test_landscape_next_ensures_playback_after_nav(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=False)
    playback_calls: list[tuple[int, str, bool]] = []

    with (
        patch("fun_time.command_dispatch.vlc_nav_step", return_value=True),
        patch("fun_time.command_dispatch.ensure_playback_state",
              side_effect=lambda p, pw, should_play: playback_calls.append((p, pw, should_play)) or True),
    ):
        dispatch_command("landscape_next", state, config)

    assert (config.landscape_port, config.vlc_password, True) in playback_calls


# --- primary_prev / primary_next ---


def test_primary_prev_calls_vlc_nav_step(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=False)
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step",
               side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        dispatch_command("primary_prev", state, config)

    assert nav_calls == [(config.primary_port, "prev")]


def test_primary_next_calls_vlc_nav_step(tmp_path: Path):
    """Primary VLC navigation uses vlc_nav_step (pl_play&id=N).
    With --start-paused removed, VLC auto-plays on item transitions."""
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=False)
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step",
               side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        dispatch_command("primary_next", state, config)

    assert nav_calls == [(config.primary_port, "next")]


# --- landscape_prev / landscape_next ---


def test_landscape_prev_cancels_lock_and_calls_nav_step_prev(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=True)
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step", side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        new_state, ops = dispatch_command("landscape_prev", state, config)

    assert new_state.locked3 is False
    assert nav_calls == [(8092, "prev")]


# --- quarter_button ---


def test_quarter_button_writes_genau_offset_command(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, ops = dispatch_command("quarter_button", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "OFFSET_QUARTER_CYCLE"


# --- omnipause_toggle ---


def test_omnipause_toggle_enters_pause_from_unpaused(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)
    playback_calls: list[tuple[int, str, bool]] = []

    def track_playback(port, password, should_play):
        playback_calls.append((port, password, should_play))
        return True

    with patch("fun_time.runtime_flow.ensure_playback_state", side_effect=track_playback):
        new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert new_state.omni_paused is True
    assert any(op.op == "suspend_hotkeys" for op in ops)
    paused_ports = [c[0] for c in playback_calls if not c[2]]
    assert config.portrait_port in paused_ports
    assert config.landscape_port in paused_ports
    assert config.primary_port in paused_ports


def test_omnipause_toggle_leaves_pause_from_paused(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)
    playback_calls: list[tuple[int, str, bool]] = []

    def track_playback(port, password, should_play):
        playback_calls.append((port, password, should_play))
        return True

    with patch("fun_time.runtime_flow.ensure_playback_state", side_effect=track_playback):
        new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert new_state.omni_paused is False
    assert any(op.op == "unsuspend_hotkeys" for op in ops)
    resumed_ports = [c[0] for c in playback_calls if c[2]]
    assert config.portrait_port in resumed_ports
    assert config.landscape_port in resumed_ports
    assert config.primary_port in resumed_ports


# --- fmode_toggle ---


def test_fmode_toggle_enables_from_disabled(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(f_mode_enabled=False)

    with (
        patch("fun_time.command_dispatch.apply_toggle_fmode") as mock_fmode,
    ):
        mock_fmode.return_value = type("R", (), {
            "success": True,
            "next_f_mode_enabled": True,
            "next_locked2": False,
            "next_locked3": False,
            "log_message": "F-mode hotkey: enabled",
        })()
        new_state, ops = dispatch_command("fmode_toggle", state, config)

    assert new_state.f_mode_enabled is True
    assert new_state.locked2 is False
    assert new_state.locked3 is False


def test_fmode_panel_click_dispatches_as_fmode_toggle(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(f_mode_enabled=False)

    with (
        patch("fun_time.command_dispatch.apply_toggle_fmode") as mock_fmode,
    ):
        mock_fmode.return_value = type("R", (), {
            "success": True,
            "next_f_mode_enabled": True,
            "next_locked2": False,
            "next_locked3": False,
            "log_message": "F-mode hotkey: enabled",
        })()
        new_state, ops = dispatch_command("fmode_panel", state, config)

    assert new_state.f_mode_enabled is True
    assert new_state.locked2 is False
    assert new_state.locked3 is False


# --- genau_toggle ---


def test_genau_toggle_deactivates_when_genau_mode_on(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=True)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("genau_toggle", state, config)

    assert new_state.genau_mode is False
    assert any(op.op == "set_topmost" and op.title == "Genau" and op.value is False for op in ops)


def test_genau_toggle_activates_when_genau_mode_off(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=False)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("genau_toggle", state, config)

    assert new_state.genau_mode is True
    assert any(op.op == "set_topmost" and op.title == "Genau" and op.value is True for op in ops)
    assert any(op.op == "activate" and op.title == "Genau" for op in ops)


def test_genau_panel_is_alias_for_genau_toggle(tmp_path: Path):
    """genau_panel dispatches identically to genau_toggle."""
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=True)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("genau_panel", state, config)

    assert new_state.genau_mode is False
    assert any(op.op == "set_topmost" and op.title == "Genau" and op.value is False for op in ops)


# --- genau command forwarding (_GENAU_CMD_MAP) ---


def test_genau_speed_down_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=True)

    new_state, ops = dispatch_command("genau_speed_down", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN"
    assert new_state == state
    assert ops == []


def test_genau_next_clip_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=True)

    new_state, ops = dispatch_command("genau_next_clip", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "NEXT"


def test_genau_cmd_noop_when_not_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=False)

    new_state, ops = dispatch_command("genau_speed_down", state, config)

    assert not config.genau_cmd_file.exists()
    assert new_state == state
    assert ops == []


def test_enter_omnipause_pauses_all_vlcs_and_suspends(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)
    playback_calls: list[tuple[int, str, bool]] = []

    def track_playback(port, password, should_play):
        playback_calls.append((port, password, should_play))
        return True

    with patch("fun_time.runtime_flow.ensure_playback_state", side_effect=track_playback):
        new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert new_state.omni_paused is True
    assert any(op.op == "suspend_hotkeys" for op in ops)
    paused_ports = [c[0] for c in playback_calls if not c[2]]
    assert config.portrait_port in paused_ports
    assert config.landscape_port in paused_ports
    assert config.primary_port in paused_ports


def test_enter_omnipause_does_not_remove_genau_topmost(tmp_path: Path):
    """Genau should stay topmost during omnipause — only pause playback."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False, genau_mode=True)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert not any(op.op == "set_topmost" and op.title == "Genau" and op.value is False for op in ops)


def test_omnipause_toggle_enter_does_not_remove_genau_topmost(tmp_path: Path):
    """Esc (omnipause toggle) should pause Genau, not remove its topmost."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False, genau_mode=True)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert not any(op.op == "set_topmost" and op.title == "Genau" and op.value is False for op in ops)


def test_leave_omnipause_skip_primary_resumes_satellites_only(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)
    playback_calls: list[tuple[int, str, bool]] = []

    def track_playback(port, password, should_play):
        playback_calls.append((port, password, should_play))
        return True

    with patch("fun_time.runtime_flow.ensure_playback_state", side_effect=track_playback):
        new_state, ops = dispatch_command("leave_omnipause_skip_primary", state, config)

    assert new_state.omni_paused is False
    assert any(op.op == "unsuspend_hotkeys" for op in ops)
    resumed_ports = [c[0] for c in playback_calls if c[2]]
    assert config.portrait_port in resumed_ports
    assert config.landscape_port in resumed_ports
    assert config.primary_port not in resumed_ports


def test_leave_omnipause_skip_primary_adds_genau_ops_when_in_robot_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True, genau_mode=True)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("leave_omnipause_skip_primary", state, config)

    assert any(op.op == "set_topmost" and op.title == "Genau" and op.value is True for op in ops)
    assert any(op.op == "activate" and op.title == "Genau" for op in ops)


# --- vlc nudge ---


def test_vlc_nudge_prev_sends_vk_left(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, ops = dispatch_command("vlc_nudge_prev", state, config)

    assert len(ops) == 1
    assert ops[0].op == "send_vk"
    assert ops[0].vk == 0x25  # VK_LEFT


def test_vlc_nudge_next_sends_vk_right(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, ops = dispatch_command("vlc_nudge_next", state, config)

    assert len(ops) == 1
    assert ops[0].op == "send_vk"
    assert ops[0].vk == 0x27  # VK_RIGHT


# --- unknown command ---


def test_unknown_command_returns_unchanged_state(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, ops = dispatch_command("bogus_command", state, config)

    assert new_state == state
    assert ops == []


# --- clipper_save ---


def test_clipper_save_calls_subprocess_when_not_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=False)

    with patch("fun_time.command_dispatch.get_current_file_path", return_value=r"C:\videos\test.mp4"), \
         patch("fun_time.command_dispatch.get_playback_time", return_value=42.5), \
         patch("fun_time.command_dispatch._clipper_python", return_value="python"), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 0
        mock_subprocess.run.return_value.stdout = r"C:\clipper\sessions\test.json"
        mock_subprocess.run.return_value.stderr = ""
        new_state, ops = dispatch_command("clipper_save", state, config)

    assert new_state == state
    mock_subprocess.run.assert_called_once()
    call_args = mock_subprocess.run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "python"
    assert "-m" in cmd
    assert "clipper.create_session" in cmd
    assert "--video" in cmd
    assert r"C:\videos\test.mp4" in cmd
    assert "--time" in cmd
    assert "42.5" in cmd
    assert len(ops) == 1
    assert ops[0].op == "tooltip"
    assert ops[0].key  # non-empty message


def test_clipper_save_no_tooltip_on_failure(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=False)

    with patch("fun_time.command_dispatch.get_current_file_path", return_value=r"C:\videos\test.mp4"), \
         patch("fun_time.command_dispatch.get_playback_time", return_value=42.5), \
         patch("fun_time.command_dispatch._clipper_python", return_value="python"), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 1
        mock_subprocess.run.return_value.stdout = ""
        mock_subprocess.run.return_value.stderr = "ffprobe failed"
        new_state, ops = dispatch_command("clipper_save", state, config)

    assert ops == []


def test_clipper_save_noop_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(genau_mode=True)

    with patch("fun_time.command_dispatch.get_current_file_path") as mock_vlc, \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_vlc.assert_not_called()
    mock_subprocess.run.assert_not_called()


def test_clipper_save_skips_when_no_video_playing(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.command_dispatch.get_current_file_path", return_value=""), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_subprocess.run.assert_not_called()


def test_clipper_save_skips_when_no_playback_time(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.command_dispatch.get_current_file_path", return_value=r"C:\videos\test.mp4"), \
         patch("fun_time.command_dispatch.get_playback_time", return_value=None), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_subprocess.run.assert_not_called()
