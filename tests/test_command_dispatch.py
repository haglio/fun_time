from __future__ import annotations

import json
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


def test_primary_prev_in_hybrid_calls_vlc_nav_step(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step",
               side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        dispatch_command("primary_prev", state, config)

    assert nav_calls == [(config.primary_port, "prev")]
    assert not config.nau_cmd_file.exists()


def test_primary_next_in_hybrid_calls_vlc_nav_step(tmp_path: Path):
    """In hybrid mode the primary VLC displays video, so navigation goes to
    it via vlc_nav_step (pl_play&id=N)."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")
    nav_calls: list[tuple] = []

    with patch("fun_time.command_dispatch.vlc_nav_step",
               side_effect=lambda p, pw, d: nav_calls.append((p, d)) or True):
        dispatch_command("primary_next", state, config)

    assert nav_calls == [(config.primary_port, "next")]


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


def test_nau_length_mode_not_written_outside_nau_mode(tmp_path: Path):
    """Length/version actions target the Nau display, so they are inert unless
    Nau owns the primary slot."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

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
    # In nau mode the primary VLC stays paused — Nau resumes via its flag file
    assert config.primary_port not in resumed_ports
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


# --- recency_order_refresh ---


def test_recency_order_refresh_keeps_recent_and_resets_locks(tmp_path: Path):
    config = _make_config(tmp_path)
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
        ("hide_role", "primary"),
    ]


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
        ("hide_role", "primary"),
    ]


def test_hybrid_activate_switches_to_hybrid(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("hybrid_activate", state, config)

    assert new_state.primary_mode == "hybrid"
    slot_ops = [(op.op, op.key) for op in ops if op.op.endswith("_role")]
    # Hybrid shows the primary VLC underneath Genau's transparent HUD.
    assert slot_ops == [
        ("show_role", "primary"),
        ("show_role", "genau"),
        ("activate_role", "genau"),
        ("hide_role", "nau"),
    ]


# --- genau command forwarding (_GENAU_CMD_MAP) ---


def test_genau_speed_down_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

    new_state, ops = dispatch_command("genau_speed_down", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN"
    assert new_state == state
    assert ops == []


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


def test_leave_omnipause_skip_primary_emits_restore_all_topmost(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("leave_omnipause_skip_primary", state, config)

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


def test_leave_omnipause_skip_primary_adds_genau_ops_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True, primary_mode="genau")

    with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
        new_state, ops = dispatch_command("leave_omnipause_skip_primary", state, config)

    assert any(op.op == "activate_role" and op.key == "genau" for op in ops)


# --- primary nudge ---


def test_primary_nudge_in_hybrid_is_a_dispatch_noop(tmp_path: Path):
    """Hybrid nudges are intercepted by the dispatch loop's seek
    accumulator before dispatch_command ever sees them; if one arrives
    anyway it must not touch Nau's command file."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    new_state, ops = dispatch_command("primary_nudge_prev", state, config)

    assert ops == []
    assert not config.nau_cmd_file.exists()


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


def test_nau_record_commands_noop_outside_nau_mode(tmp_path: Path):
    config = _make_config(tmp_path)

    for mode in ("genau", "hybrid"):
        state = _make_state(primary_mode=mode)
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
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

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
    state = _make_state(primary_mode="hybrid")

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
    state = _make_state(primary_mode="genau")

    with patch("fun_time.command_dispatch.get_current_file_path") as mock_vlc, \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_vlc.assert_not_called()
    mock_subprocess.run.assert_not_called()


def test_clipper_save_skips_when_no_video_playing(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    with patch("fun_time.command_dispatch.get_current_file_path", return_value=""), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_subprocess.run.assert_not_called()


def test_clipper_save_skips_when_no_playback_time(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="hybrid")

    with patch("fun_time.command_dispatch.get_current_file_path", return_value=r"C:\videos\test.mp4"), \
         patch("fun_time.command_dispatch.get_playback_time", return_value=None), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
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
