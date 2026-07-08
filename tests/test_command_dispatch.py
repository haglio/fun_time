from __future__ import annotations

import json
from dataclasses import replace
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
        nau_cmd_file=state_dir / "nau_cmd.txt",
        nau_paused_file=state_dir / "nau_paused.txt",
        nau_status_file=state_dir / "nau_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
    )


def _make_state(**overrides) -> BridgeState:
    defaults = dict(
        locked2=False,
        locked3=False,
        primary_mode="nau",
        f_mode_enabled=False,
        omni_paused=False,
        recency_order=False,
    )
    defaults.update(overrides)
    return BridgeState(**defaults)


# --- open_rfb_tab on lock ---


def test_portrait_lock_emits_open_rfb_tab_op_for_known_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=r"C:\videos\provider2\abc_123.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.ensure_in_favs"),
    ):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is True
    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert rfb_ops[0].key == "https://example.net/image/abc"


def test_portrait_lock_records_a_lock_watch_event(tmp_path: Path):
    """Locking is the strongest 'I like this' signal — it must feed the
    watch stats that drive playback frequency."""
    from fun_time.media_metadata import normalize_path_key
    from fun_time.watch_stats import load_watch_stats

    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    video = tmp_path / "clip.mp4"
    video.write_text("x", encoding="utf-8")

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=str(video)),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.ensure_in_favs"),
    ):
        dispatch_command("portrait_lock", state, config)

    stats = load_watch_stats(config.state_dir / "watch_stats.json")
    assert stats[normalize_path_key(str(video))]["locks"] == 1


def test_portrait_unlock_records_no_watch_event(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=str(tmp_path / "clip.mp4")),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.vlc_http_cmd", return_value=True),
    ):
        dispatch_command("portrait_lock", state, config)

    assert not (config.state_dir / "watch_stats.json").exists()


def test_portrait_lock_no_open_rfb_tab_op_for_unknown_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=r"C:\videos\other\xyz.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.ensure_in_favs"),
    ):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is True
    assert not any(op.op == "open_rfb_tab" for op in ops)


def test_portrait_lock_no_open_rfb_tab_op_when_unlocking(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=r"C:\videos\provider2\abc_123.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.vlc_http_cmd", return_value=True),
    ):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is False
    assert not any(op.op == "open_rfb_tab" for op in ops)


def test_landscape_lock_emits_open_rfb_tab_op_for_known_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=False)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=r"C:\videos\provider\def_456.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.ensure_in_favs"),
    ):
        new_state, ops = dispatch_command("landscape_lock", state, config)

    assert new_state.locked3 is True
    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert rfb_ops[0].key == "https://example.com/image/def"


def test_landscape_lock_emits_provider_regen_url_when_metadata_present(tmp_path: Path):
    """A locked Provider video with a metadata sidecar opens the generate page
    (prompts in the #ft fragment), not the dead /image/{id} gallery link."""
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    rel = Path("2_outbox") / "upscaled_by_orientation" / "landscape" / "provider"
    video = media_root / rel / "vid_topaz.mp4"
    meta_file = metadata_root / rel / "vid_topaz.json"
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps({"video": {"prompt": "hi", "model": "Realism"}}), encoding="utf-8")
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")

    config = _make_config(tmp_path)
    config.provider_media_root = media_root
    config.provider_metadata_root = metadata_root
    state = _make_state(locked3=False)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=str(video)),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.ensure_in_favs"),
    ):
        new_state, ops = dispatch_command("landscape_lock", state, config)

    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert rfb_ops[0].key.startswith("https://example.com/video#ft=")


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


def test_primary_prev_in_hybrid_writes_nau_cmd(tmp_path: Path):
    """Hybrid displays Nau, so navigation goes to Nau's command file, not VLC."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    dispatch_command("primary_prev", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "PREV"


def test_primary_next_in_hybrid_writes_nau_cmd(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    dispatch_command("primary_next", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "NEXT"


def test_primary_next_in_nau_mode_writes_nau_cmd(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step",
               side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        dispatch_command("primary_next", state, config)

    assert nav_calls == []
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "NEXT"


def test_primary_prev_in_genau_mode_writes_nau_cmd(tmp_path: Path):
    """Outside hybrid, Nau is the primary player — even while Genau mode is
    active, [ and ] navigate the paused Nau in the background."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    dispatch_command("primary_prev", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "PREV"


# --- nau cycle-version / length-mode ---


def test_nau_cycle_version_writes_nau_cmd(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    dispatch_command("nau_cycle_version", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "CYCLE_VERSION"


def test_nau_toggle_length_writes_toggle_command(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    dispatch_command("nau_toggle_length", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "TOGGLE_LENGTH_MODE"


def test_nau_length_shorts_writes_set_length_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    dispatch_command("nau_length_shorts", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_LENGTH_MODE shorts"


def test_nau_length_full_writes_set_length_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    dispatch_command("nau_length_full", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_LENGTH_MODE full"


def test_nau_length_mode_written_in_hybrid_mode(tmp_path: Path):
    """Nau owns the display in hybrid too, so length/version actions apply."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    dispatch_command("nau_length_shorts", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_LENGTH_MODE shorts"


def test_nau_length_mode_not_written_in_genau_mode(tmp_path: Path):
    """Length/version actions are inert in genau mode, where Genau owns the display."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    dispatch_command("nau_length_shorts", state, config)

    assert not config.nau_cmd_file.exists()


# --- landscape_prev / landscape_next ---


def test_landscape_prev_cancels_lock_and_calls_nav_step_prev(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=True)
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step", side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        new_state, ops = dispatch_command("landscape_prev", state, config)

    assert new_state.locked3 is False
    assert nav_calls == [(8092, "prev")]


# --- active side tracking ---


def test_portrait_command_sets_active_side_to_portrait(tmp_path: Path):
    """A portrait command marks portrait as the active side, so a later
    side-agnostic command ('lock', 'next', ...) knows which player to hit."""
    config = _make_config(tmp_path)
    state = _make_state(active_side=3)  # currently on landscape

    with (
        patch("fun_time.command_dispatch.vlc_nav_step", return_value=True),
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        new_state, _ops = dispatch_command("portrait_next", state, config)

    assert new_state.active_side == 2


def test_landscape_command_sets_active_side_to_landscape(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(active_side=2, locked3=False)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value="C:\\clips\\l.mp4"),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True),
        patch("fun_time.command_dispatch.ensure_in_favs"),
        patch("fun_time.command_dispatch.vlc_http_cmd", return_value=True),
    ):
        new_state, _ops = dispatch_command("landscape_lock", state, config)

    assert new_state.active_side == 3


def test_primary_nav_sets_active_side_to_primary(tmp_path: Path):
    """Navigating the primary (Nau) makes it the active player (slot 1), so bare
    'next'/'previous' then drive it too."""
    config = _make_config(tmp_path)

    for command in ("primary_next", "primary_prev"):
        new_state, _ops = dispatch_command(command, _make_state(active_side=3), config)
        assert new_state.active_side == 1, command


def test_nudge_and_mode_commands_leave_active_side_unchanged(tmp_path: Path):
    """Only next/prev nav marks the active player: nudges, mode, and genau
    commands must not disturb the remembered side."""
    config = _make_config(tmp_path)

    for command in ("primary_nudge_next", "primary_nudge_prev", "hybrid_activate"):
        new_state, _ops = dispatch_command(command, _make_state(active_side=3), config)
        assert new_state.active_side == 3, command


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
    assert config.nau_paused_file.read_text(encoding="utf-8") == "0"


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


def test_fmode_toggle_passes_current_recency_order(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(f_mode_enabled=False, recency_order=True)

    with patch("fun_time.command_dispatch.apply_toggle_fmode") as mock_fmode:
        mock_fmode.return_value = type("R", (), {
            "success": True,
            "next_f_mode_enabled": True,
            "next_locked2": False,
            "next_locked3": False,
            "log_message": "F-mode hotkey: enabled",
        })()
        dispatch_command("fmode_toggle", state, config)

    assert mock_fmode.call_args.kwargs["recent"] is True


def test_fmode_toggle_passes_provider_roots_for_group_collapse(tmp_path: Path):
    config = replace(
        _make_config(tmp_path),
        provider_media_root=tmp_path / "media",
        provider_metadata_root=tmp_path / "metadata",
    )
    state = _make_state(f_mode_enabled=False)

    with patch("fun_time.command_dispatch.apply_toggle_fmode") as mock_fmode:
        mock_fmode.return_value = type("R", (), {
            "success": True,
            "next_f_mode_enabled": True,
            "next_locked2": False,
            "next_locked3": False,
            "log_message": "F-mode hotkey: enabled",
        })()
        dispatch_command("fmode_toggle", state, config)

    kwargs = mock_fmode.call_args.kwargs
    assert kwargs["provider_media_root"] == tmp_path / "media"
    assert kwargs["provider_metadata_root"] == tmp_path / "metadata"


def _filter_result(count=1, applied=True, message="ok"):
    return type("R", (), {"count": count, "applied": applied, "log_message": message})()


def test_filter_command_scopes_to_one_satellite(tmp_path: Path):
    config = replace(
        _make_config(tmp_path),
        provider_media_root=tmp_path / "media",
        provider_metadata_root=tmp_path / "metadata",
    )
    state = _make_state()

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        new_state, ops = dispatch_command("filter_portrait_alpha", state, config)

    assert new_state.portrait_filter == "alpha"
    assert new_state.landscape_filter == ""  # the other VLC is untouched
    assert mock_filter.call_count == 1
    kwargs = mock_filter.call_args.kwargs
    assert kwargs["which"] == 2
    assert kwargs["query"] == "alpha"
    assert kwargs["port"] == config.portrait_port
    assert kwargs["sources"] == config.portrait_sources
    assert kwargs["provider_media_root"] == tmp_path / "media"
    assert any(op.op == "tooltip" for op in ops)


def test_filter_command_both_scope_rebuilds_each_satellite(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        new_state, _ops = dispatch_command("filter_both_beta_gamma", state, config)

    assert new_state.portrait_filter == "beta gamma"
    assert new_state.landscape_filter == "beta gamma"
    assert {call.kwargs["which"] for call in mock_filter.call_args_list} == {2, 3}


def test_zero_match_filter_is_not_recorded_in_state(tmp_path: Path):
    # A filter that matched nothing kept the current playlist, so it must not be
    # recorded — otherwise a later premiere/F-mode rebuild would blank the VLC.
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=0, applied=False)
        new_state, _ops = dispatch_command("filter_portrait_alpha", state, config)

    assert new_state.portrait_filter == ""


def test_clear_filter_command_resets_only_its_scope(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait_filter="alpha", landscape_filter="kissing")

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        new_state, _ops = dispatch_command("filter_portrait_clear", state, config)

    assert new_state.portrait_filter == ""
    assert new_state.landscape_filter == "kissing"  # untouched
    assert mock_filter.call_args.kwargs["query"] == ""


def test_fmode_toggle_passes_active_filters(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait_filter="alpha", landscape_filter="kissing")

    with patch("fun_time.command_dispatch.apply_toggle_fmode") as mock_fmode:
        mock_fmode.return_value = type("R", (), {
            "success": True, "next_f_mode_enabled": True,
            "next_locked2": False, "next_locked3": False, "log_message": "x",
        })()
        dispatch_command("fmode_toggle", state, config)

    kwargs = mock_fmode.call_args.kwargs
    assert kwargs["portrait_filter"] == "alpha"
    assert kwargs["landscape_filter"] == "kissing"


def test_premiere_refresh_passes_active_filters_and_roots(tmp_path: Path):
    config = replace(
        _make_config(tmp_path),
        provider_media_root=tmp_path / "media",
        provider_metadata_root=tmp_path / "metadata",
    )
    state = _make_state(portrait_filter="alpha", landscape_filter="kissing")

    with patch("fun_time.command_dispatch.apply_refresh_recency_order") as mock_recency:
        mock_recency.return_value = type("R", (), {
            "next_recency_order": True, "next_locked2": False,
            "next_locked3": False, "log_message": "x",
        })()
        dispatch_command("recency_order_refresh", state, config)

    kwargs = mock_recency.call_args.kwargs
    assert kwargs["portrait_filter"] == "alpha"
    assert kwargs["landscape_filter"] == "kissing"
    assert kwargs["provider_media_root"] == tmp_path / "media"


# --- portrait/landscape cycle action & cycle seed ---


def _cycle_meta(image_seed: str, action: str) -> dict:
    return {
        "video": {"prompt": f"do {action}", "action": action, "seed": "5"},
        "source_image": {"positive_prompt": "subject at the beach", "seed": image_seed},
    }


def _make_grouped_config(tmp_path: Path, videos: dict[str, dict | None]) -> tuple[BridgeConfig, dict[str, str]]:
    """A BridgeConfig whose portrait source dir holds *videos* (+ sidecars)."""
    from fun_time.media_metadata import metadata_path_for, reset_group_index_cache

    reset_group_index_cache()
    media_root = tmp_path / "media"
    metadata_root = tmp_path / "metadata"
    portrait_dir = media_root / "portrait"
    portrait_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, meta in videos.items():
        video = portrait_dir / f"{name}.mp4"
        video.write_text("x", encoding="utf-8")
        paths[name] = str(video)
        if meta is not None:
            sidecar = metadata_path_for(video, media_root, metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(meta), encoding="utf-8")
    config = replace(
        _make_config(tmp_path),
        portrait_sources=str(portrait_dir),
        provider_media_root=media_root,
        provider_metadata_root=metadata_root,
    )
    return config, paths


def test_portrait_cycle_action_swaps_to_next_action_of_the_group(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
        "other_subject": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["subject_zeta"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=([(3, paths["subject_zeta"])], 3)),
        patch("fun_time.command_dispatch.vlc_swap_current_with", return_value=True) as swap,
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        _new_state, ops = dispatch_command("portrait_cycle_action", state, config)

    swap.assert_called_once_with(config.portrait_port, "pw", paths["subject_alpha"])
    tooltips = [op.key for op in ops if op.op == "tooltip"]
    assert tooltips == ["Action: Alpha"]


def test_portrait_cycle_action_jumps_when_sibling_already_in_playlist(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
    })
    state = _make_state()
    entries = [(3, paths["subject_zeta"]), (4, paths["subject_alpha"])]

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["subject_zeta"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=(entries, 3)),
        patch("fun_time.command_dispatch.vlc_play_playlist_item", return_value=True) as play,
        patch("fun_time.command_dispatch.vlc_swap_current_with") as swap,
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        dispatch_command("portrait_cycle_action", state, config)

    play.assert_called_once_with(config.portrait_port, "pw", 4)
    swap.assert_not_called()


def test_portrait_cycle_action_tooltips_when_video_has_no_siblings(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "loner": _cycle_meta("111", "Alpha"),
    })
    state = _make_state()

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["loner"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=([(3, paths["loner"])], 3)),
        patch("fun_time.command_dispatch.vlc_swap_current_with") as swap,
    ):
        _new_state, ops = dispatch_command("portrait_cycle_action", state, config)

    swap.assert_not_called()
    assert [op.key for op in ops if op.op == "tooltip"] == ["No other actions"]


def test_portrait_cycle_action_keeps_an_active_lock(tmp_path: Path):
    """Cycling is 'show me this subject differently', not 'move on' — a lock
    (and its repeat-one) must survive and apply to the sibling."""
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
    })
    state = _make_state(locked2=True)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["subject_zeta"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=([(3, paths["subject_zeta"])], 3)),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True) as repeat,
        patch("fun_time.command_dispatch.vlc_swap_current_with", return_value=True),
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        new_state, _ops = dispatch_command("portrait_cycle_action", state, config)

    assert new_state.locked2 is True
    repeat.assert_not_called()


def test_portrait_cycle_seed_keeps_an_active_lock(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state(locked2=True)
    entries = [(3, paths["subject_a"]), (5, paths["subject_b"])]

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["subject_a"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=(entries, 3)),
        patch("fun_time.command_dispatch.set_repeat_mode", return_value=True) as repeat,
        patch("fun_time.command_dispatch.vlc_play_playlist_item", return_value=True),
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        new_state, _ops = dispatch_command("portrait_cycle_seed", state, config)

    assert new_state.locked2 is True
    repeat.assert_not_called()


def test_portrait_cycle_seed_jumps_to_sister_seed_in_playlist(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
        "subject_a_other_action": _cycle_meta("111", "Zeta Massage"),
    })
    state = _make_state()
    entries = [
        (3, paths["subject_a"]),
        (4, paths["subject_a_other_action"]),  # same seed as current: not a target
        (5, paths["subject_b"]),
    ]

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["subject_a"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=(entries, 3)),
        patch("fun_time.command_dispatch.vlc_play_playlist_item", return_value=True) as play,
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        dispatch_command("portrait_cycle_seed", state, config)

    play.assert_called_once_with(config.portrait_port, "pw", 5)


def test_portrait_cycle_seed_swaps_in_library_sister_when_none_in_playlist(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["subject_a"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=([(3, paths["subject_a"])], 3)),
        patch("fun_time.command_dispatch.vlc_swap_current_with", return_value=True) as swap,
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    swap.assert_called_once_with(config.portrait_port, "pw", paths["subject_b"])
    # An exact same-config sister is not a widened match, so it carries no label.
    assert "Similar clip" not in [op.key for op in ops if op.op == "tooltip"]


def test_portrait_cycle_seed_tooltips_without_seed_siblings(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "no_meta": None,
    })
    state = _make_state()

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["subject_a"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=([(3, paths["subject_a"])], 3)),
        patch("fun_time.command_dispatch.vlc_swap_current_with") as swap,
    ):
        _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    swap.assert_not_called()
    assert [op.key for op in ops if op.op == "tooltip"] == ["No other seeds"]


def _scene_meta(*, image_seed: str, quality: str) -> dict:
    """Same beach scene as its kin, but a render knob (image quality) set — so
    two such metas share a loose family yet split into separate strict ones."""
    return {
        "video": {"prompt": "beach", "action": "Alpha", "seed": "5"},
        "source_image": {"positive_prompt": "subject at the beach", "seed": image_seed, "quality": quality},
    }


def test_portrait_cycle_seed_widens_to_a_near_match_when_no_exact_sister(tmp_path: Path):
    """No same-config sister exists (a render knob differs), so 'seed' widens the
    net to the same-scene clip instead of giving up with 'No other seeds'."""
    config, paths = _make_grouped_config(tmp_path, {
        "subject_best": _scene_meta(image_seed="111", quality="Best"),
        "subject_draft": _scene_meta(image_seed="222", quality="Draft"),
    })
    state = _make_state()

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=paths["subject_best"]),
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=([(3, paths["subject_best"])], 3)),
        patch("fun_time.command_dispatch.vlc_swap_current_with", return_value=True) as swap,
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    swap.assert_called_once_with(config.portrait_port, "pw", paths["subject_draft"])
    # A widened hit is flagged so it reads as a near-match, not an exact seed sister.
    assert [op.key for op in ops if op.op == "tooltip"] == ["Similar clip"]


def test_landscape_cycle_commands_target_the_landscape_player(tmp_path: Path):
    """The landscape variants must hit the landscape port and lock flag."""
    from fun_time.media_metadata import metadata_path_for, reset_group_index_cache

    reset_group_index_cache()
    config, paths = _make_grouped_config(tmp_path, {})
    media_root = config.provider_media_root
    landscape_dir = media_root / "landscape"
    landscape_dir.mkdir(parents=True, exist_ok=True)
    videos: dict[str, str] = {}
    for name, meta in (
        ("subject_zeta", _cycle_meta("111", "Zeta Massage")),
        ("subject_alpha", _cycle_meta("111", "Alpha")),
    ):
        video = landscape_dir / f"{name}.mp4"
        video.write_text("x", encoding="utf-8")
        videos[name] = str(video)
        sidecar = metadata_path_for(video, media_root, config.provider_metadata_root)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(meta), encoding="utf-8")
    config = replace(config, landscape_sources=str(landscape_dir))
    state = _make_state(locked3=True)

    with (
        patch("fun_time.command_dispatch.get_current_file_path", return_value=videos["subject_zeta"]) as current,
        patch("fun_time.command_dispatch.get_playlist_entries", return_value=([(3, videos["subject_zeta"])], 3)) as entries,
        patch("fun_time.command_dispatch.vlc_swap_current_with", return_value=True) as swap,
        patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
    ):
        new_state, _ops = dispatch_command("landscape_cycle_action", state, config)

    assert new_state.locked3 is True, "cycling must not release the landscape lock"
    current.assert_called_with(config.landscape_port, "pw")
    entries.assert_called_once_with(config.landscape_port, "pw")
    swap.assert_called_once_with(config.landscape_port, "pw", videos["subject_alpha"])


# --- recency_order_refresh ---


def test_recency_order_refresh_keeps_recent_and_resets_locks(tmp_path: Path):
    config = _make_config(tmp_path)
    config.provider_media_root = tmp_path / "media"
    config.provider_metadata_root = tmp_path / "metadata"
    # Already in Premiere: pressing again must keep newest-first, never toggle off.
    state = _make_state(recency_order=True, locked2=True, locked3=True)

    with patch("fun_time.command_dispatch.apply_refresh_recency_order") as mock_recency:
        mock_recency.return_value = type("R", (), {
            "next_recency_order": True,
            "next_locked2": False,
            "next_locked3": False,
            "log_message": "Premiere: Portrait/Landscape reloaded newest-first",
        })()
        new_state, ops = dispatch_command("recency_order_refresh", state, config)

    assert new_state.recency_order is True
    assert new_state.locked2 is False
    assert new_state.locked3 is False
    kwargs = mock_recency.call_args.kwargs
    # The refresh always targets newest-first, so it takes no prior-order input.
    assert "recency_order" not in kwargs
    assert kwargs["f_mode_enabled"] is False
    assert kwargs["portrait_port"] == config.portrait_port
    assert kwargs["landscape_port"] == config.landscape_port
    # Premiere must collapse action groups too, so the provider roots flow through.
    assert kwargs["provider_media_root"] == config.provider_media_root
    assert kwargs["provider_metadata_root"] == config.provider_metadata_root


# --- mode switch (genau_activate / vlc_activate / hybrid_activate) ---


def test_nau_activate_deactivates_genau_and_raises_nau(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("nau_activate", state, config)

    assert new_state.primary_mode == "nau"
    slot_ops = [(op.op, op.key) for op in ops if op.op.endswith("_role")]
    # Nau is shown and activated BEFORE the old slot-mates hide, so focus
    # never falls through to another application.
    assert slot_ops == [
        ("show_role", "nau"),
        ("activate_role", "nau"),
        ("hide_role", "genau"),
    ]
    # The mode switch re-stacks the Nau/Genau pair for the new mode.
    assert [op.op for op in ops if op.op == "restack_primary"] == ["restack_primary"]


def test_genau_activate_activates_genau_and_lowers_nau(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("genau_activate", state, config)

    assert new_state.primary_mode == "genau"
    slot_ops = [(op.op, op.key) for op in ops if op.op.endswith("_role")]
    assert slot_ops == [
        ("show_role", "genau"),
        ("activate_role", "genau"),
        ("hide_role", "nau"),
    ]
    # The mode switch re-stacks the Nau/Genau pair for the new mode.
    assert [op.op for op in ops if op.op == "restack_primary"] == ["restack_primary"]


def test_hybrid_activate_switches_to_hybrid(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("hybrid_activate", state, config)

    assert new_state.primary_mode == "hybrid"
    slot_ops = [(op.op, op.key) for op in ops if op.op.endswith("_role")]
    # Hybrid shows Nau underneath Genau's transparent HUD (Genau drives the OSR2).
    assert slot_ops == [
        ("show_role", "nau"),
        ("show_role", "genau"),
        ("activate_role", "genau"),
    ]
    # The mode switch re-stacks the pair — Nau topmost with Genau's HUD above it.
    assert [op.op for op in ops if op.op == "restack_primary"] == ["restack_primary"]


def test_leaving_hybrid_reenables_nau_tcode(tmp_path: Path):
    """Leaving hybrid re-enables Nau's funscript T-Code — the per-video gap
    arbiter may have muted it, and nau mode must drive its funscript again."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    dispatch_command("nau_activate", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_TCODE_ENABLED 1"


# --- genau command forwarding (_GENAU_CMD_MAP) ---


def test_genau_speed_down_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_speed_down", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN"
    assert new_state == state
    assert ops == []


def test_speed_up_routes_to_nau_in_nau_mode(tmp_path: Path):
    # In nau mode Nau drives the OSR2, so the speed keys steer its video rate;
    # Nau's mpv clock drives the funscript, so it scales along.
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    new_state, ops = dispatch_command("genau_speed_up", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SPEED_UP"
    assert not config.genau_cmd_file.exists()
    assert new_state == state
    assert ops == []


def test_speed_down_routes_to_nau_in_nau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    dispatch_command("genau_speed_down", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN"
    assert not config.genau_cmd_file.exists()


def _set_nau_driving(config, *, driving: bool) -> None:
    """Publish a Nau status so the hybrid speed arbiter sees the funscript
    driving (scripted, not resting) or not."""
    config.nau_status_file.write_text(
        f"has_funscript={'1' if driving else '0'}\nfunscript_resting=0\n",
        encoding="utf-8",
    )


def test_speed_routes_to_nau_in_hybrid_while_funscript_drives(tmp_path: Path):
    # Hybrid, actively scripted stretch: the funscript (Nau) drives the OSR2, so
    # speed tunes Nau's video, which scales the driving script.
    config = _make_config(tmp_path)
    _set_nau_driving(config, driving=True)
    state = _make_state(primary_mode="hybrid")

    dispatch_command("genau_speed_up", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SPEED_UP"
    assert not config.genau_cmd_file.exists()


def test_speed_routes_to_genau_in_hybrid_while_genau_drives(tmp_path: Path):
    # Hybrid, unscripted stretch (no funscript / lead-in / gap): Genau drives the
    # OSR2, so speed tunes Genau's stroke rate.
    config = _make_config(tmp_path)
    _set_nau_driving(config, driving=False)
    state = _make_state(primary_mode="hybrid")

    dispatch_command("genau_speed_up", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_UP"
    assert not config.nau_cmd_file.exists()


def test_speed_min_and_max_route_to_the_active_engine(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("speed_min", _make_state(primary_mode="nau"), config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_SPEED min"

    config = _make_config(tmp_path)
    dispatch_command("speed_max", _make_state(primary_mode="genau"), config)
    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED 100"


def test_nau_multiplier_sets_nau_speed(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("nau_speed_150", _make_state(primary_mode="nau"), config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_SPEED 1.5"


def test_nau_multiplier_is_a_noop_when_genau_drives(tmp_path: Path):
    # An absolute multiplier is a Nau-video concept; in genau mode Nau is hidden,
    # so it is a no-op (the speaker uses Genau's own 0-100 grammar there).
    config = _make_config(tmp_path)
    dispatch_command("nau_speed_150", _make_state(primary_mode="genau"), config)
    assert not config.genau_cmd_file.exists()
    assert not config.nau_cmd_file.exists()


def test_absolute_speed_reaches_nau_video_in_hybrid_even_when_genau_drives(tmp_path: Path):
    # Absolute video-speed sets (multiplier, min/max) tune whatever Nau shows, so
    # they land on Nau's video even during a Genau-driven stretch — they must not
    # silently vanish the way a driver-routed command would.
    config = _make_config(tmp_path)
    _set_nau_driving(config, driving=False)  # Genau owns the OSR2 this stretch
    state = _make_state(primary_mode="hybrid")

    dispatch_command("nau_speed_150", state, config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_SPEED 1.5"
    assert not config.genau_cmd_file.exists()


def test_speed_max_sets_nau_video_in_hybrid(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_nau_driving(config, driving=False)
    dispatch_command("speed_max", _make_state(primary_mode="hybrid"), config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_SPEED max"


def test_reset_speed_command_maps_to_normal_rate(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("nau_speed_100", _make_state(primary_mode="nau"), config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_SPEED 1"


def test_genau_next_clip_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_next_clip", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "NEXT"


def test_genau_toggle_auto_flips_genau_enabled_flag(tmp_path: Path):
    from fun_time.command_dispatch import genau_enabled_path, read_genau_enabled
    config = _make_config(tmp_path)
    state = _make_state()
    flag = genau_enabled_path(config.state_dir)

    # Missing flag means takeover allowed; first toggle suppresses it.
    assert read_genau_enabled(flag) is True
    dispatch_command("genau_toggle_auto", state, config)
    assert flag.read_text(encoding="utf-8").strip() == "0"
    assert read_genau_enabled(flag) is False

    # Toggling again re-allows takeover.
    dispatch_command("genau_toggle_auto", state, config)
    assert flag.read_text(encoding="utf-8").strip() == "1"
    assert read_genau_enabled(flag) is True


def test_genau_toggle_auto_does_not_write_genau_cmd_file(tmp_path: Path):
    # It must drive the broker flag, not the (unrelated) Genau command file.
    config = _make_config(tmp_path)
    dispatch_command("genau_toggle_auto", _make_state(), config)
    assert not config.genau_cmd_file.exists()


def test_genau_cycle_shape_prev_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_cycle_shape_prev", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "CYCLE_SHAPE_PREV"
    assert new_state == state
    assert ops == []


def test_genau_toggle_cruise_writes_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_toggle_cruise", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "TOGGLE_CRUISE"
    assert new_state == state
    assert ops == []


def test_genau_cruise_on_writes_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_cruise_on", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "CRUISE_ON"
    assert new_state == state
    assert ops == []


def test_genau_cruise_off_writes_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_cruise_off", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "CRUISE_OFF"
    assert new_state == state
    assert ops == []


def test_genau_cmd_noop_when_not_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    new_state, ops = dispatch_command("genau_speed_down", state, config)

    assert not config.genau_cmd_file.exists()
    assert new_state == state
    assert ops == []


# --- genau numeric command forwarding ---


def test_genau_amp_writes_numeric_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_amp_50", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "AMP 50"
    assert new_state == state
    assert ops == []


def test_genau_center_writes_numeric_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_center_80", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "CENTER 80"


def test_genau_speed_writes_numeric_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_speed_30", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED 30"


def test_genau_numeric_cmd_noop_when_not_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    new_state, ops = dispatch_command("genau_amp_50", state, config)

    assert not config.genau_cmd_file.exists()
    assert new_state == state
    assert ops == []


def test_enter_omnipause_pauses_satellites_and_suspends(tmp_path: Path):
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


def test_enter_omnipause_emits_disable_all_topmost(tmp_path: Path):
    """Entering omnipause must emit a disable_all_topmost op so the dispatch
    loop removes topmost from all windows."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert any(op.op == "disable_all_topmost" for op in ops)


def test_omnipause_toggle_enter_emits_disable_all_topmost(tmp_path: Path):
    """Toggle entering omnipause must also emit disable_all_topmost."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert any(op.op == "disable_all_topmost" for op in ops)


def test_omnipause_toggle_leave_emits_restore_all_topmost(tmp_path: Path):
    """Leaving omnipause must emit restore_all_topmost so the dispatch
    loop rebuilds the z-order stack."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert any(op.op == "restore_all_topmost" for op in ops)


def test_leave_omnipause_emits_restore_all_topmost(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("leave_omnipause", state, config)

    assert any(op.op == "restore_all_topmost" for op in ops)


def test_enter_omnipause_does_not_remove_genau_topmost(tmp_path: Path):
    """Genau should stay topmost during omnipause — only pause playback."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False, primary_mode="genau")

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert not any(op.op == "hide_role" and op.key == "genau" for op in ops)


def test_omnipause_toggle_enter_does_not_remove_genau_topmost(tmp_path: Path):
    """Esc (omnipause toggle) should pause Genau, not remove its topmost."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False, primary_mode="genau")

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert not any(op.op == "hide_role" and op.key == "genau" for op in ops)


def test_leave_omnipause_resumes_satellites_only(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)
    playback_calls: list[tuple[int, str, bool]] = []

    def track_playback(port, password, should_play):
        playback_calls.append((port, password, should_play))
        return True

    with patch("fun_time.runtime_flow.ensure_playback_state", side_effect=track_playback):
        new_state, ops = dispatch_command("leave_omnipause", state, config)

    assert new_state.omni_paused is False
    assert any(op.op == "unsuspend_hotkeys" for op in ops)
    resumed_ports = [c[0] for c in playback_calls if c[2]]
    assert config.portrait_port in resumed_ports
    assert config.landscape_port in resumed_ports


def test_leave_omnipause_adds_genau_ops_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True, primary_mode="genau")

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("leave_omnipause", state, config)

    assert any(op.op == "activate_role" and op.key == "genau" for op in ops)


# --- primary nudge ---


def test_primary_nudge_in_hybrid_writes_nau_seek(tmp_path: Path):
    """Hybrid displays Nau, so nudges seek Nau just like in nau mode."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    new_state, ops = dispatch_command("primary_nudge_prev", state, config)

    assert ops == []
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SEEK_BACK"


def test_primary_nudge_in_nau_mode_writes_nau_seek(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    new_state, ops = dispatch_command("primary_nudge_prev", state, config)
    assert ops == []
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SEEK_BACK"

    dispatch_command("primary_nudge_next", state, config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SEEK_FWD"


# --- nau record commands ---


def test_nau_record_commands_write_nau_cmd_in_nau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    for command, expected in [
        ("nau_record_down", "RECORD_DOWN"),
        ("nau_record_up", "RECORD_UP"),
        ("nau_record_tap", "RECORD_TAP"),
        ("nau_loop_cancel", "LOOP_CANCEL"),
    ]:
        new_state, ops = dispatch_command(command, state, config)
        assert config.nau_cmd_file.read_text(encoding="utf-8") == expected
        assert ops == []
        config.nau_cmd_file.unlink()


def test_nau_record_commands_work_in_hybrid_mode(tmp_path: Path):
    """Hybrid displays Nau, so loop recording works there too."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    new_state, ops = dispatch_command("nau_record_tap", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "RECORD_TAP"
    assert ops == []


def test_nau_record_commands_noop_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("nau_record_tap", state, config)

    assert not config.nau_cmd_file.exists()
    assert ops == []


# --- unknown command ---


def test_unknown_command_returns_unchanged_state(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, ops = dispatch_command("bogus_command", state, config)

    assert new_state == state
    assert ops == []


# --- clipper_save ---


def test_clipper_save_calls_subprocess_in_hybrid_mode(tmp_path: Path):
    """Hybrid displays Nau, so clipper reads the current video/time from Nau's status."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")
    config.nau_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\nstate=normal\npaused=0\n",
        encoding="utf-8",
    )

    with patch("fun_time.command_dispatch._clipper_python", return_value="python"), \
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
    state = _make_state(primary_mode="hybrid")
    config.nau_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.command_dispatch._clipper_python", return_value="python"), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 1
        mock_subprocess.run.return_value.stdout = ""
        mock_subprocess.run.return_value.stderr = "ffprobe failed"
        new_state, ops = dispatch_command("clipper_save", state, config)

    assert ops == []


def test_clipper_save_noop_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    with patch("fun_time.command_dispatch.get_current_file_path") as mock_vlc, \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_vlc.assert_not_called()
    mock_subprocess.run.assert_not_called()


def test_clipper_save_skips_when_no_video_playing(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")
    # No nau_status file → no current video → nothing to clip.

    with patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_subprocess.run.assert_not_called()


def test_clipper_save_in_nau_mode_uses_nau_status(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")
    config.nau_status_file.write_text(
        "video=C:\\videos\\naustuff.mp4\n"
        "position_ms=42500\n"
        "duration_ms=60000\n"
        "has_funscript=1\n"
        "state=normal\n"
        "paused=0\n",
        encoding="utf-8",
    )

    with patch("fun_time.command_dispatch.get_current_file_path") as mock_vlc, \
         patch("fun_time.command_dispatch._clipper_python", return_value="python"), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 0
        mock_subprocess.run.return_value.stdout = "C:\\clipper\\sessions\\naustuff.json"
        mock_subprocess.run.return_value.stderr = ""
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_vlc.assert_not_called()
    cmd = mock_subprocess.run.call_args[0][0]
    assert "C:\\videos\\naustuff.mp4" in cmd
    assert "42.5" in cmd
    assert len(ops) == 1 and ops[0].op == "tooltip"


def test_clipper_save_in_nau_mode_skips_without_status(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    with patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_subprocess.run.assert_not_called()
    assert ops == []
