from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import url2pathname

import logging

from fun_time.command_dispatch import (
    FAILED_NOTICE_LEVEL,
    BridgeState,
    BridgeConfig,
    WindowOp,
    dispatch_command,
)
from fun_time.event_log import NOTICE
from fun_time.media_metadata import GroupIndex, normalize_path_key


def _make_config(tmp_path: Path) -> BridgeConfig:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text("local_file,web_url\n", encoding="utf-8")
    weird_dir = tmp_path / "weird"
    weird_dir.mkdir(exist_ok=True)
    return BridgeConfig(
        portrait_cmd_file=state_dir / "portrait_cmd.txt",
        portrait_paused_file=state_dir / "portrait_paused.txt",
        portrait_status_file=state_dir / "portrait_status.txt",
        portrait_playlist_file=state_dir / "portrait_playlist.tsv",
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        landscape_playlist_file=state_dir / "landscape_playlist.tsv",
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
        audio_volume_file=state_dir / "audio_volume.txt",
        nau_cmd_file=state_dir / "nau_cmd.txt",
        nau_paused_file=state_dir / "nau_paused.txt",
        nau_status_file=state_dir / "nau_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
        broker_cmd_file=state_dir / "broker_cmd.txt",
    )


def _set_current(config: BridgeConfig, which: int, video: str, *, locked: bool = False) -> None:
    """Make read_satellite_status report *video* as satellite *which*'s current
    clip — the file-based stand-in for the old get_current_file_path mock."""
    status = config.satellite_status_file(which)
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(
        f"video={video}\nposition_ms=100\nduration_ms=1000\n"
        f"paused=0\nlocked={'1' if locked else '0'}\n",
        encoding="utf-8",
    )


def _cmds(config: BridgeConfig, which: int) -> list[str]:
    """The verbs queued on satellite *which*'s command file, in order."""
    cmd_file = config.satellite_cmd_file(which)
    if not cmd_file.exists():
        return []
    return [line.strip() for line in cmd_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _playlist(config: BridgeConfig, which: int) -> list[str]:
    """The video paths written to satellite *which*'s playlist file, in order."""
    playlist = config.satellite_playlist_file(which)
    if not playlist.exists():
        return []
    return [
        line.split("\t")[0]
        for line in playlist.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_state(**overrides) -> BridgeState:
    defaults = dict(
        locked2=False,
        locked3=False,
        primary_mode="nau",
        f_mode_enabled=False,
        omni_paused=False,
    )
    defaults.update(overrides)
    return BridgeState(**defaults)


# --- open_rfb_tab on lock ---


def _tab_page(op_key: str) -> str:
    """The HTML of the landing page an open_rfb_tab op points at."""
    assert op_key.startswith("file:///"), op_key
    return Path(url2pathname(urlparse(op_key).path)).read_text(encoding="utf-8")


def test_portrait_lock_opens_a_landing_page_not_the_site(tmp_path: Path):
    """Lock defers the load behind the same Ctrl+R page the RFB's own tabs use."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)

    from fun_time.media_actions import WEB_PROVIDERS, make_web_url_from_path

    path = rf"C:\videos\{WEB_PROVIDERS[0].marker}\abc_123.mp4"
    _set_current(config, 2, path)
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is True
    assert _cmds(config, 2) == ["LOCK"]
    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert f'"{make_web_url_from_path(path)}"' in _tab_page(rfb_ops[0].key)


def test_lock_landing_page_plays_the_locked_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    from fun_time.media_actions import WEB_PROVIDERS

    video = tmp_path / "videos" / WEB_PROVIDERS[0].marker / "abc_123.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")

    _set_current(config, 2, str(video))
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        _, ops = dispatch_command("portrait_lock", state, config)

    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert f'"{video.as_uri()}"' in _tab_page(rfb_ops[0].key)


def test_locking_the_same_video_twice_reuses_one_landing_page(tmp_path: Path):
    config = _make_config(tmp_path)

    keys = []
    for _ in range(2):
        from fun_time.media_actions import WEB_PROVIDERS

        _set_current(config, 2, rf"C:\videos\{WEB_PROVIDERS[0].marker}\abc_123.mp4")
        with patch("fun_time.command_dispatch.ensure_in_favs"):
            _, ops = dispatch_command("portrait_lock", _make_state(locked2=False), config)
        keys += [op.key for op in ops if op.op == "open_rfb_tab"]

    assert keys[0] == keys[1]
    assert len(list((config.state_dir / "rfb_tabs").glob("*.html"))) == 1


def test_portrait_lock_records_a_lock_watch_event(tmp_path: Path):
    """Locking is the strongest 'I like this' signal — it must feed the
    watch stats that drive playback frequency."""
    from fun_time.media_metadata import normalize_path_key
    from fun_time.watch_stats import load_watch_stats

    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    video = tmp_path / "clip.mp4"
    video.write_text("x", encoding="utf-8")

    _set_current(config, 2, str(video))
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        dispatch_command("portrait_lock", state, config)

    stats = load_watch_stats(config.state_dir / "watch_stats.json")
    assert stats[normalize_path_key(str(video))]["locks"] == 1


def test_portrait_unlock_records_no_watch_event(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    _set_current(config, 2, str(tmp_path / "clip.mp4"))
    dispatch_command("portrait_lock", state, config)

    assert _cmds(config, 2) == ["UNLOCK", "NEXT"]
    assert not (config.state_dir / "watch_stats.json").exists()


def test_portrait_lock_no_open_rfb_tab_op_for_unknown_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)

    _set_current(config, 2, r"C:\videos\other\xyz.mp4")
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is True
    assert not any(op.op == "open_rfb_tab" for op in ops)


def test_portrait_lock_no_open_rfb_tab_op_when_unlocking(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    _set_current(config, 2, r"C:\videos\provider2\abc_123.mp4")
    new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is False
    assert not any(op.op == "open_rfb_tab" for op in ops)


def test_landscape_lock_emits_open_rfb_tab_op_for_known_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=False)

    from fun_time.media_actions import WEB_PROVIDERS, make_web_url_from_path

    path = rf"C:\videos\{WEB_PROVIDERS[0].marker}\def_456.mp4"
    _set_current(config, 3, path)
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        new_state, ops = dispatch_command("landscape_lock", state, config)

    assert new_state.locked3 is True
    assert _cmds(config, 3) == ["LOCK"]
    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert f'"{make_web_url_from_path(path)}"' in _tab_page(rfb_ops[0].key)


def test_landscape_lock_emits_regen_url_when_metadata_present(tmp_path: Path):
    """A locked Provider video with a metadata sidecar opens the generate page
    (prompts in the #ft fragment), not the dead /image/{id} gallery link."""
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    rel = Path("2_outbox") / "upscaled_by_orientation" / "landscape" / "provider"
    video = media_root / rel / "vid_topaz.mp4"
    meta_file = metadata_root / "2D" / "AI" / rel / "vid_topaz.json"
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps({"video": {"prompt": "hi", "model": "Realism"}}), encoding="utf-8")
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")

    config = _make_config(tmp_path)
    config.regen_media_root = media_root
    config.regen_metadata_root = metadata_root
    state = _make_state(locked3=False)

    _set_current(config, 3, str(video))
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        new_state, ops = dispatch_command("landscape_lock", state, config)

    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert '"https://example.com/video#ft=' in _tab_page(rfb_ops[0].key)


# --- portrait_lock ---


def test_portrait_lock_toggles_lock_on(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)

    _set_current(config, 2, "C:\\clips\\portrait.mp4")
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is True
    assert new_state.locked3 is False
    assert _cmds(config, 2) == ["LOCK"]


def test_portrait_lock_toggles_lock_off(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    _set_current(config, 2, "C:\\clips\\portrait.mp4")
    new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.locked2 is False
    assert _cmds(config, 2) == ["UNLOCK", "NEXT"]


# --- landscape_lock ---


def test_landscape_lock_toggles_lock_on(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=False)

    _set_current(config, 3, "C:\\clips\\landscape.mp4")
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        new_state, ops = dispatch_command("landscape_lock", state, config)

    assert new_state.locked3 is True
    assert _cmds(config, 3) == ["LOCK"]


# --- back-dating a spoken command to the video it was meant for ---


def test_portrait_lock_returns_to_the_video_that_was_playing_when_spoken(tmp_path: Path):
    """A phrase is only recognized once the speaker stops, so an auto-advancing
    satellite can be a video on by then.  Locking brings back the video the user
    was actually looking at, and locks that."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    meant = "C:\\clips\\meant.mp4"
    now_playing = "C:\\clips\\advanced_to.mp4"

    _set_current(config, 2, now_playing)
    with patch("fun_time.command_dispatch.ensure_in_favs") as favs:
        new_state, _ops = dispatch_command("portrait_lock", state, config, target_path=meant)

    # Back-dated: bring the spoken video back (PLAY_FILE) then LOCK it.
    assert _cmds(config, 2) == [f"PLAY_FILE {meant}", "LOCK"]
    assert favs.call_args[0][1] == meant
    assert new_state.locked2 is True


def test_portrait_trash_discards_the_video_that_was_playing_when_spoken(tmp_path: Path):
    """"Weird" condemns the video the speaker saw.  Once the satellite has moved
    on there is nothing to advance past — the condemned video is dropped from the
    playlist where it sits, and the innocent video now playing is left alone."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)
    meant = "C:\\clips\\meant.mp4"
    now_playing = "C:\\clips\\advanced_to.mp4"
    _set_current(config, 2, now_playing)
    with (
        patch("fun_time.command_dispatch.remove_from_favs") as favs,
        patch("fun_time.command_dispatch.move_to_weird") as weird,
    ):
        dispatch_command("portrait_trash", state, config, target_path=meant)

    # Back-dated: jump back to the condemned clip (PLAY_FILE) then TRASH it where
    # it sits; the innocent clip now playing is left alone.
    assert _cmds(config, 2) == [f"PLAY_FILE {meant}", "TRASH"]
    assert favs.call_args[0][1] == meant
    assert weird.call_args[0][1] == Path(meant)


# --- portrait_trash ---


def test_portrait_trash_unlocks_and_discards(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    _set_current(config, 2, "C:\\clips\\portrait.mp4")
    with (
        patch("fun_time.command_dispatch.remove_from_favs"),
        patch("fun_time.command_dispatch.move_to_weird"),
    ):
        new_state, ops = dispatch_command("portrait_trash", state, config)

    assert new_state.locked2 is False
    assert _cmds(config, 2) == ["UNLOCK", "TRASH"]


def test_portrait_trash_queues_a_trash_verb_and_condemns_the_clip(tmp_path: Path):
    """An unlocked discard queues a bare TRASH (the native player drops the
    current clip and plays the next) and condemns the clip — favs removal + weird."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)

    _set_current(config, 2, "C:\\clips\\portrait.mp4")
    with (
        patch("fun_time.command_dispatch.remove_from_favs") as favs,
        patch("fun_time.command_dispatch.move_to_weird") as weird,
    ):
        dispatch_command("portrait_trash", state, config)

    assert _cmds(config, 2) == ["TRASH"]
    assert favs.call_args[0][1] == "C:\\clips\\portrait.mp4"
    assert weird.call_args[0][1] == Path("C:\\clips\\portrait.mp4")


# --- portrait_prev / portrait_next ---


def test_portrait_prev_cancels_lock_and_queues_prev(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)

    new_state, ops = dispatch_command("portrait_prev", state, config)

    assert new_state.locked2 is False
    # A locked side unlocks first (repeat-one off), then steps back.
    assert _cmds(config, 2) == ["UNLOCK", "PREV"]


def test_portrait_next_queues_next(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked2=False)

    dispatch_command("portrait_next", state, config)

    assert _cmds(config, 2) == ["NEXT"]


# --- primary_prev / primary_next ---


def test_primary_prev_in_hybrid_writes_nau_cmd(tmp_path: Path):
    """Hybrid displays Nau, so navigation goes to Nau's command file, not a satellite's."""
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

    dispatch_command("primary_next", state, config)

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


def test_end_compilation_writes_end_compilation(tmp_path: Path):
    """Out of a compilation without naming a length — Nau goes back to whichever
    mode it was in when it entered."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    dispatch_command("nau_end_compilation", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "END_COMPILATION"


def test_nau_length_mixed_writes_set_length_mode(tmp_path: Path):
    """The unfiltered mode Nau opens in, and the way back to it from either half."""
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    dispatch_command("nau_length_mixed", state, config)

    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_LENGTH_MODE mixed"


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


# --- clip navigation (compilation / full vid / money shot) ---


def test_compilation_writes_play_compilation(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("nau_compilation", _make_state(primary_mode="nau"), config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "PLAY_COMPILATION"


def test_full_vid_writes_play_full_vid(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("nau_full_vid", _make_state(primary_mode="hybrid"), config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "PLAY_FULL_VID"


def test_money_shot_writes_play_money_shot(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("nau_money_shot", _make_state(primary_mode="nau"), config)
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "PLAY_MONEY_SHOT"


def test_clip_nav_inert_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("nau_compilation", _make_state(primary_mode="genau"), config)
    assert not config.nau_cmd_file.exists()


def test_clip_nav_voice_phrases():
    from fun_time.voice_commands import VOICE_COMMANDS

    assert VOICE_COMMANDS["compilation"] == "nau_compilation"
    assert VOICE_COMMANDS["full video"] == "nau_full_vid"
    assert VOICE_COMMANDS["money shot"] == "nau_money_shot"
    assert VOICE_COMMANDS["redacted"] == "nau_money_shot"


# --- landscape_prev / landscape_next ---


def test_landscape_prev_cancels_lock_and_queues_prev(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=True)

    new_state, ops = dispatch_command("landscape_prev", state, config)

    assert new_state.locked3 is False
    assert _cmds(config, 3) == ["UNLOCK", "PREV"]


# --- active side tracking ---


def test_portrait_command_sets_active_side_to_portrait(tmp_path: Path):
    """A portrait command marks portrait as the active side, so a later
    side-agnostic command ('lock', 'next', ...) knows which player to hit."""
    config = _make_config(tmp_path)
    state = _make_state(active_side=3)  # currently on landscape

    new_state, _ops = dispatch_command("portrait_next", state, config)

    assert new_state.active_side == 2


def test_landscape_command_sets_active_side_to_landscape(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(active_side=2, locked3=False)

    _set_current(config, 3, "C:\\clips\\l.mp4")
    with patch("fun_time.command_dispatch.ensure_in_favs"):
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

    new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert new_state.omni_paused is True
    assert any(op.op == "suspend_hotkeys" for op in ops)
    # OmniPause freezes the native satellites by writing their paused flag files.
    assert config.portrait_paused_file.read_text(encoding="utf-8") == "1"
    assert config.landscape_paused_file.read_text(encoding="utf-8") == "1"


def test_omnipause_toggle_leaves_pause_from_paused(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert new_state.omni_paused is False
    assert any(op.op == "unsuspend_hotkeys" for op in ops)
    # Leaving OmniPause clears both satellites' paused flags and resumes Nau.
    assert config.portrait_paused_file.read_text(encoding="utf-8") == "0"
    assert config.landscape_paused_file.read_text(encoding="utf-8") == "0"
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


def test_fmode_toggle_passes_each_sides_current_order(tmp_path: Path):
    """F-mode rebuilds both satellites, and the two can be in different orders, so
    each side's own ordering has to go with it."""
    config = _make_config(tmp_path)
    state = _make_state(f_mode_enabled=False, portrait_latest=True, landscape_latest=False)

    with patch("fun_time.command_dispatch.apply_toggle_fmode") as mock_fmode:
        mock_fmode.return_value = type("R", (), {
            "success": True,
            "next_f_mode_enabled": True,
            "next_locked2": False,
            "next_locked3": False,
            "log_message": "F-mode hotkey: enabled",
        })()
        dispatch_command("fmode_toggle", state, config)

    assert mock_fmode.call_args.kwargs["portrait_recent"] is True
    assert mock_fmode.call_args.kwargs["landscape_recent"] is False


def test_fmode_toggle_passes_provider_roots_for_group_collapse(tmp_path: Path):
    config = replace(
        _make_config(tmp_path),
        regen_media_root=tmp_path / "media",
        regen_metadata_root=tmp_path / "metadata",
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
    assert kwargs["regen_media_root"] == tmp_path / "media"
    assert kwargs["regen_metadata_root"] == tmp_path / "metadata"


def _filter_result(count=1, applied=True, message="ok"):
    return type("R", (), {"count": count, "applied": applied, "log_message": message})()


def test_filter_command_scopes_to_one_satellite(tmp_path: Path):
    config = replace(
        _make_config(tmp_path),
        regen_media_root=tmp_path / "media",
        regen_metadata_root=tmp_path / "metadata",
    )
    state = _make_state()

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        new_state, ops = dispatch_command("filter_portrait_alpha", state, config)

    assert new_state.portrait_filter == "alpha"
    assert new_state.landscape_filter == ""  # the other satellite is untouched
    assert mock_filter.call_count == 1
    kwargs = mock_filter.call_args.kwargs
    assert kwargs["which"] == 2
    assert kwargs["query"] == "alpha"
    assert kwargs["cmd_file"] == config.satellite_cmd_file(2)
    assert kwargs["sources"] == config.portrait_sources
    assert kwargs["regen_media_root"] == tmp_path / "media"
    assert any(op.op == "notice" for op in ops)


def test_no_loop_returns_to_browse_keeping_the_filter(tmp_path: Path):
    """"no loop" reshapes the queue back to the browse but re-uses the
    satellite's own filter so it survives — unlike reset, which clears it."""
    config = _make_config(tmp_path)
    state = _make_state(portrait_filter="alpha")

    with patch("fun_time.command_dispatch.satellite_browse_paths", return_value=["C:/v/x.mp4"]) as mock_browse:
        new_state, ops = dispatch_command("portrait_no_loop", state, config)

    # The browse is built with the CURRENT filter (kept), not cleared to "".
    assert mock_browse.call_args.kwargs["query"] == "alpha"
    assert new_state.portrait_filter == "alpha"
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)
    assert [op.key for op in ops if op.op == "notice"] == ["Loop off"]


def test_play_video_command_switches_the_satellite_to_the_path(tmp_path: Path):
    """A HUD thumbnail click sends "<side>_play_video|<path>"; the satellite
    switches straight to that clip via the same play helper cycling uses, and
    clicking it makes that satellite the active side."""
    config = _make_config(tmp_path)
    state = _make_state()
    path = "C:/vids/portrait/pick_me.mp4"

    new_state, ops = dispatch_command(f"portrait_play_video|{path}", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {path}"]
    assert new_state.active_side == 2
    assert [op.source for op in ops if op.op == "notice"] == ["portrait"]


def test_lock_video_command_when_already_locked_switches_and_stays_locked(tmp_path: Path):
    """A HUD double-click sends "<side>_lock_video|<path>": on an already-locked
    satellite (repeat-one) it just plays the picked clip, which keeps it locked."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=True)
    path = "C:/vids/portrait/lock_me.mp4"

    new_state, _ops = dispatch_command(f"portrait_lock_video|{path}", state, config)

    # Already repeat-one: the double-click just moves the lock onto the picked clip.
    assert _cmds(config, 2) == [f"PLAY_FILE {path}"]
    assert new_state.locked2 is True


# --- HUD keyboard navigation ---


def _nav_config(tmp_path: Path) -> tuple[BridgeConfig, dict[str, str]]:
    """A grouped portrait library with a seed row (subject_b) and an action column
    (subject_a_zeta) around subject_a — the map keyboard navigation walks."""
    return _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),           # corner
        "subject_b": _cycle_meta("222", "Alpha"),           # seed sibling (same act, other subject)
        "subject_a_zeta": _cycle_meta("111", "Zeta Massage"),  # action sibling (same subject, other act)
    })


def test_nav_right_switches_to_the_first_seed_and_freezes_the_anchor(tmp_path: Path):
    """Shift+Right from the corner selects the first seed, switches the satellite
    to it (like a thumbnail click) and freezes the map on the start clip."""
    config, paths = _nav_config(tmp_path)
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    new_state, ops = dispatch_command("portrait_nav_right", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b']}"]
    assert new_state.portrait_nav_anchor == paths["subject_a"]
    assert new_state.active_side == 2
    assert [op.source for op in ops if op.op == "notice"] == ["portrait"]


def test_nav_down_switches_to_the_first_action(tmp_path: Path):
    config, paths = _nav_config(tmp_path)
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_a_zeta']}"]


def test_nav_continues_across_the_seed_row_from_the_frozen_anchor(tmp_path: Path):
    """A second Shift+Right, with the anchor still frozen on subject_a and the
    satellite now on seed subject_b, steps to the next seed — traversal the frozen
    anchor makes possible even though a plain switch would re-home the map."""
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
        "subject_c": _cycle_meta("333", "Alpha"),
    })
    state = _make_state(portrait_nav_anchor=paths["subject_a"])

    _set_current(config, 2, paths["subject_b"])
    new_state, _ops = dispatch_command("portrait_nav_right", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_c']}"]
    assert new_state.portrait_nav_anchor == paths["subject_a"]  # the anchor held


def test_nav_past_the_last_seed_wraps_round_to_the_anchor(tmp_path: Path):
    """The seed row is a ring the anchor heads, so Shift+Right off its last seed
    comes back round to the anchor instead of stopping — hold the key and you
    tour the row."""
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state(portrait_nav_anchor=paths["subject_a"])

    _set_current(config, 2, paths["subject_b"])  # the row's only seed — its last
    new_state, _ops = dispatch_command("portrait_nav_right", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_a']}"]
    assert new_state.portrait_nav_anchor == paths["subject_a"]  # the ring kept its head


def test_nav_onto_an_axis_with_nowhere_to_go_is_a_dead_end(tmp_path: Path):
    """Wrapping needs somewhere to wrap to.  These clips share an act, so the
    anchor has no other action to step down onto: nothing switches and it reads
    red."""
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state(portrait_nav_anchor=paths["subject_a"])

    _set_current(config, 2, paths["subject_a"])
    _new_state, ops = dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == []  # an empty axis: nothing switched
    dead_end = [op for op in ops if op.op == "notice"]
    assert dead_end and dead_end[0].level == FAILED_NOTICE_LEVEL


def test_nav_re_anchors_after_the_satellite_drifts_off_the_map(tmp_path: Path):
    """A stale anchor whose map no longer holds the live clip (an auto-advance)
    is abandoned: navigation re-anchors on whatever is now playing and steps from
    there."""
    config, paths = _nav_config(tmp_path)
    # Frozen on a bogus anchor subject_a_zeta is not subject_a's; the live clip
    # subject_a is not on subject_a_zeta's seed/action map, so it re-anchors on subject_a.
    state = _make_state(portrait_nav_anchor="C:/vids/portrait/gone.mp4")

    _set_current(config, 2, paths["subject_a"])
    new_state, _ops = dispatch_command("portrait_nav_right", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b']}"]
    assert new_state.portrait_nav_anchor == paths["subject_a"]  # re-anchored on the live clip


def test_nav_lock_locks_the_current_clip_and_clears_the_anchor(tmp_path: Path):
    """Enter locks the selected (current) clip and drops the frozen anchor, so the
    HUD re-homes its map on the freshly locked clip."""
    config, paths = _nav_config(tmp_path)
    state = _make_state(portrait_nav_anchor=paths["subject_a"], locked2=False)

    _set_current(config, 2, paths["subject_b"])
    with (
        patch("fun_time.command_dispatch.ensure_in_favs"),
        patch("fun_time.command_dispatch.record_watch_event"),
    ):
        new_state, _ops = dispatch_command("portrait_nav_lock", state, config)

    assert new_state.locked2 is True
    assert new_state.portrait_nav_anchor == ""


def test_a_non_nav_side_command_clears_the_nav_anchor(tmp_path: Path):
    """Any side command that is not a navigation step ends navigation, so the map
    stops freezing and re-homes on the live clip."""
    config = _make_config(tmp_path)
    state = _make_state(portrait_nav_anchor="C:/vids/portrait/anchor.mp4")

    new_state, _ops = dispatch_command("portrait_next", state, config)

    assert new_state.portrait_nav_anchor == ""


def test_landscape_nav_sets_the_active_side(tmp_path: Path):
    """A landscape nav key (Shift+WASD) makes landscape the active side, so a
    later bare Enter (active_nav_lock) resolves to it."""
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, _ops = dispatch_command("landscape_nav_right", state, config)

    assert new_state.active_side == 3


def test_filter_command_both_scope_rebuilds_each_satellite(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        new_state, _ops = dispatch_command("filter_both_beta_gamma", state, config)

    assert new_state.portrait_filter == "beta gamma"
    assert new_state.landscape_filter == "beta gamma"
    assert {call.kwargs["which"] for call in mock_filter.call_args_list} == {2, 3}


def test_filter_command_both_scope_notices_each_satellite_under_its_own_source(tmp_path: Path):
    """A both-scope filter touches two windows, so it reports two notices — one
    filed under each — rather than one line the log panel cannot filter."""
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        _new_state, ops = dispatch_command("filter_both_beta_gamma", state, config)

    notices = [op for op in ops if op.op == "notice"]
    assert [op.source for op in notices] == ["portrait", "landscape"]


def test_zero_match_filter_is_not_recorded_in_state(tmp_path: Path):
    # A filter that matched nothing kept the current playlist, so it must not be
    # recorded — otherwise a later reorder/F-mode rebuild would blank the satellite.
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


def test_recents_passes_the_sides_filter_and_roots(tmp_path: Path):
    config = replace(
        _make_config(tmp_path),
        regen_media_root=tmp_path / "media",
        regen_metadata_root=tmp_path / "metadata",
    )
    state = _make_state(portrait_filter="alpha", landscape_filter="kissing")

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        dispatch_command("portrait_latest", state, config)

    kwargs = mock_filter.call_args.kwargs
    assert kwargs["recent"] is True  # Latest = newest-first
    assert kwargs["query"] == "alpha"  # its own filter is kept
    assert kwargs["regen_media_root"] == tmp_path / "media"


def test_recents_reorders_only_the_side_it_names(tmp_path: Path):
    """"portrait premiere" used not to parse at all — the ordering was global.  A
    sided reorder reloads that satellite and leaves the other one alone."""
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        state, ops = dispatch_command("portrait_latest", _make_state(), config)

    assert [call.kwargs["which"] for call in mock_filter.call_args_list] == [2]
    assert state.portrait_latest is True
    assert state.landscape_latest is False
    assert [op.source for op in ops if op.op == "notice"] == ["portrait"]


def test_shuffle_puts_one_side_back_without_touching_the_other(tmp_path: Path):
    """Latest' counterpart has to be sided too, or a side reloaded newest-first
    could never be shuffled back on its own."""
    config = _make_config(tmp_path)
    state = _make_state(portrait_latest=True, landscape_latest=True)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        new_state, _ops = dispatch_command("landscape_shuffle", state, config)

    assert mock_filter.call_args.kwargs["recent"] is False
    assert new_state.landscape_latest is False
    assert new_state.portrait_latest is True  # the other side keeps its order


def test_a_sided_reorder_drops_that_sides_lock_and_loop(tmp_path: Path):
    """The rebuild replaces the queue, so whatever the side was holding — a lock, a
    group loop, a widened row — goes with it."""
    config = _make_config(tmp_path)
    state = _make_state(locked2=True, portrait_loop="seed",
                        portrait_map_anchor="C:/v/a.mp4", portrait_widen_clip="C:/v/a.mp4")

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        new_state, _ops = dispatch_command("portrait_latest", state, config)

    assert new_state.locked2 is False
    assert new_state.portrait_loop == ""
    assert new_state.portrait_map_anchor == ""
    assert new_state.portrait_widen_clip == ""


def test_a_reorder_starts_the_side_at_the_top_of_the_new_order(tmp_path: Path):
    """The bug this fixes: "portrait latest" did reorder the queue, but the player
    kept playing the clip it was on and carried on from there — so the new order only
    ever applied behind it and the newest arrivals, the whole point of asking, were
    never reached."""
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        dispatch_command("portrait_latest", _make_state(), config)

    assert mock_filter.call_args.kwargs["start_at_top"] is True


def test_a_filter_leaves_the_clip_on_screen_where_it_is(tmp_path: Path):
    """Filtering is not "start over": the reload keeps the clip playing while it
    survives the new list."""
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        dispatch_command("filter_portrait_alpha", _make_state(), config)

    assert mock_filter.call_args.kwargs["start_at_top"] is False


def test_reset_returns_the_side_to_every_default(tmp_path: Path):
    """"reset" puts the whole side back, not just its filter: the lock released, the
    order shuffled, any loop / widened row / frozen map dropped, and playing from the
    top of a fresh browse."""
    config = _make_config(tmp_path)
    state = _make_state(
        locked2=True, portrait_filter="alpha", portrait_latest=True,
        portrait_loop="seed", portrait_map_anchor="C:/v/a.mp4",
        portrait_widen_clip="C:/v/a.mp4", portrait_nav_anchor="C:/v/a.mp4",
    )

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        new_state, _ops = dispatch_command("portrait_reset", state, config)

    assert new_state.locked2 is False
    assert new_state.portrait_filter == ""
    assert new_state.portrait_latest is False
    assert new_state.portrait_loop == ""
    assert new_state.portrait_map_anchor == ""
    assert new_state.portrait_widen_clip == ""
    assert new_state.portrait_nav_anchor == ""
    assert mock_filter.call_args.kwargs["query"] == ""
    assert mock_filter.call_args.kwargs["recent"] is False
    assert mock_filter.call_args.kwargs["start_at_top"] is True
    assert "UNLOCK" in _cmds(config, 2)


def test_reset_leaves_the_other_side_alone(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(locked3=True, landscape_filter="kissing", landscape_latest=True)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        new_state, _ops = dispatch_command("portrait_reset", state, config)

    assert new_state.locked3 is True
    assert new_state.landscape_filter == "kissing"
    assert new_state.landscape_latest is True


def test_no_filter_drops_the_filter_and_nothing_else(tmp_path: Path):
    """The narrow gesture: the filter goes and the side keeps the order it was
    browsing in, so it is not a way to lose your place in a Latest browse."""
    config = _make_config(tmp_path)
    state = _make_state(portrait_filter="alpha", portrait_latest=True)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        new_state, _ops = dispatch_command("portrait_no_filter", state, config)

    assert new_state.portrait_filter == ""
    assert new_state.portrait_latest is True          # still its Latest order
    assert mock_filter.call_args.kwargs["query"] == ""
    assert mock_filter.call_args.kwargs["recent"] is True


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
    media_root = tmp_path / "videos" / "videos"  # the metadata tree mirrors this
    metadata_root = tmp_path / "videos" / "metadata"
    portrait_dir = media_root / "portrait"
    portrait_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, meta in videos.items():
        video = portrait_dir / f"{name}.mp4"
        video.write_text("x", encoding="utf-8")
        paths[name] = str(video)
        if meta is not None:
            sidecar = metadata_path_for(video, metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(meta), encoding="utf-8")
    config = replace(
        _make_config(tmp_path),
        portrait_sources=str(portrait_dir),
        regen_media_root=media_root,
        regen_metadata_root=metadata_root,
    )
    return config, paths


def test_portrait_cycle_action_swaps_to_next_action_of_the_group(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
        "other_subject": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_zeta"])
    _new_state, ops = dispatch_command("portrait_cycle_action", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_alpha']}"]
    notices = [(op.key, op.source) for op in ops if op.op == "notice"]
    assert notices == [("Action: Alpha", "portrait")]


def test_portrait_cycle_action_cycles_the_video_that_was_playing_when_spoken(tmp_path: Path):
    """"Action" asks for another take on the video the speaker had in front of
    them, so the siblings are the back-dated video's, not the newcomer's."""
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
        "other_subject": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["other_subject"])
    dispatch_command("portrait_cycle_action", state, config, target_path=paths["subject_zeta"])

    # The siblings are the back-dated (spoken) video's, not the newcomer's.
    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_alpha']}"]


def test_portrait_cycle_action_notices_when_video_has_no_siblings(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "loner": _cycle_meta("111", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["loner"])
    _new_state, ops = dispatch_command("portrait_cycle_action", state, config)

    assert _cmds(config, 2) == []
    dead_end = [op for op in ops if op.op == "notice"]
    assert [op.key for op in dead_end] == ["No other actions"]
    # A command that hit a dead end reads red, not green.
    assert dead_end[0].level == FAILED_NOTICE_LEVEL == logging.ERROR


def test_a_successful_cycle_action_notice_is_an_ordinary_green_notice(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_zeta"])
    _new_state, ops = dispatch_command("portrait_cycle_action", state, config)

    notices = [op for op in ops if op.op == "notice"]
    assert notices and all(op.level == NOTICE for op in notices)


def test_portrait_cycle_action_keeps_an_active_lock(tmp_path: Path):
    """Cycling is 'show me this subject differently', not 'move on' — a lock
    (and its repeat-one) must survive and apply to the sibling."""
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
    })
    state = _make_state(locked2=True)

    _set_current(config, 2, paths["subject_zeta"])
    new_state, _ops = dispatch_command("portrait_cycle_action", state, config)

    assert new_state.locked2 is True
    # Cycling is "show this differently", not "move on": it switches the clip but
    # never releases the lock, so no UNLOCK is queued.
    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_alpha']}"]


def test_portrait_cycle_seed_keeps_an_active_lock(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state(locked2=True)

    _set_current(config, 2, paths["subject_a"])
    new_state, _ops = dispatch_command("portrait_cycle_seed", state, config)

    assert new_state.locked2 is True
    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b']}"]


def test_portrait_cycle_seed_jumps_to_sister_seed_in_playlist(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
        "subject_a_other_action": _cycle_meta("111", "Zeta Massage"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    dispatch_command("portrait_cycle_seed", state, config)

    # subject_a_other_action shares the current seed (skipped); the sister is subject_b.
    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b']}"]


def test_portrait_cycle_seed_swaps_in_library_sister_when_none_in_playlist(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b']}"]
    # Every seed hit narrates itself so the log/flash shows what happened; an exact
    # sister reads "Next seed", which is the contrast that makes a widen legible.
    assert [op.key for op in ops if op.op == "notice"] == ["Next seed"]


def test_portrait_cycle_seed_notices_without_seed_siblings(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "no_meta": None,
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    assert _cmds(config, 2) == []
    dead_end = [op for op in ops if op.op == "notice"]
    assert [op.key for op in dead_end] == ["No other seeds"]
    assert dead_end[0].level == FAILED_NOTICE_LEVEL


def _scene_meta(*, image_seed: str, quality: str) -> dict:
    """Same beach scene as its kin, but a render knob (image quality) set — so
    two such metas are near-matches yet split into separate seed families."""
    return {
        "video": {"prompt": "beach", "action": "Alpha", "seed": "5"},
        "source_image": {"positive_prompt": "subject at the beach", "seed": image_seed, "quality": quality},
    }


def test_portrait_cycle_seed_no_longer_auto_widens(tmp_path: Path):
    """Now that the HUD shows which sisters exist, plain cycle-seed dead-ends
    instead of auto-widening — the widening moved to the "more seeds" command."""
    config, paths = _make_grouped_config(tmp_path, {
        "subject_best": _scene_meta(image_seed="111", quality="Best"),
        "subject_draft": _scene_meta(image_seed="222", quality="Draft"),  # near-match only
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_best"])
    _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    assert _cmds(config, 2) == []
    dead = [op for op in ops if op.op == "notice"]
    assert [op.key for op in dead] == ["No other seeds"]
    assert dead[0].level == FAILED_NOTICE_LEVEL


def test_more_seeds_widens_the_display_without_switching_the_video(tmp_path: Path):
    """"more seeds" never jumps to another clip — it records that this clip's seed
    row is widened (which the HUD reads to grow the row in place) and loops that
    row, which reshapes the queue but leaves the clip on screen playing."""
    cur, other = tmp_path / "cur.mp4", tmp_path / "other.mp4"
    cur.write_text("x", encoding="utf-8")
    other.write_text("x", encoding="utf-8")
    c, o = str(cur), str(other)
    kc, ko = normalize_path_key(c), normalize_path_key(o)
    config = _make_config(tmp_path)
    index = GroupIndex(
        action_key_by_path={kc: "g1", ko: "g2"},   # different subjects
        action_members={"g1": [c], "g2": [o]},
        action_by_path={kc: "Gamma", ko: "Gamma"},   # same act
        seed_key_by_path={}, seed_members={},
        path_by_key={kc: c, ko: o},
        scene_tags_by_path={kc: frozenset({"a", "b"}), ko: frozenset({"a", "c"})},
    )

    _set_current(config, 2, c)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        state, ops = dispatch_command("portrait_more_seeds", _make_state(), config)

    assert not any(cmd.startswith("PLAY_FILE") for cmd in _cmds(config, 2))  # no jump
    assert _playlist(config, 2)[0] == c   # the clip on screen leads the looped queue
    assert state.portrait_widen_clip == c
    assert [op.key for op in ops if op.op == "notice"] == ["More seeds"]


def test_more_seeds_still_widens_when_nothing_shares_the_act_or_a_tag(tmp_path: Path):
    """Widening must always turn up another video.  Even a clip that shares no
    action and no prompt tag with anything still widens — to the nearest thing
    there is — instead of dead-ending on "Widening net failed"."""
    cur, other = tmp_path / "cur.mp4", tmp_path / "other.mp4"
    cur.write_text("x", encoding="utf-8")
    other.write_text("x", encoding="utf-8")
    c, o = str(cur), str(other)
    kc, ko = normalize_path_key(c), normalize_path_key(o)
    config = _make_config(tmp_path)
    index = GroupIndex(
        action_key_by_path={kc: "g1", ko: "g2"},
        action_members={"g1": [c], "g2": [o]},
        action_by_path={kc: "Zeta", ko: "Alpha"},   # nothing else does Zeta
        seed_key_by_path={}, seed_members={},
        path_by_key={kc: c, ko: o},
        scene_tags_by_path={kc: frozenset({"a"}), ko: frozenset({"z"})},   # no shared tag
    )

    _set_current(config, 2, c)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        state, ops = dispatch_command("portrait_more_seeds", _make_state(), config)

    assert state.portrait_widen_clip == c
    assert [op.key for op in ops if op.op == "notice"] == ["More seeds"]


def test_more_seeds_reports_widening_failed_only_when_the_library_holds_one_clip(tmp_path: Path):
    """The one real dead end: there is no other video to widen to."""
    config = _make_config(tmp_path)
    only = tmp_path / "only.mp4"
    only.write_text("x", encoding="utf-8")
    key = normalize_path_key(str(only))
    index = GroupIndex(
        action_key_by_path={key: "g"}, action_members={"g": [str(only)]},
        action_by_path={key: "Alpha"},
        seed_key_by_path={}, seed_members={},
        path_by_key={key: str(only)},
    )

    _set_current(config, 2, str(only))
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        state, ops = dispatch_command("portrait_more_seeds", _make_state(), config)

    assert state.portrait_widen_clip == ""  # nothing widened
    notices = [op for op in ops if op.op == "notice"]
    assert [op.key for op in notices] == ["Widening net failed"]
    assert notices[0].level == FAILED_NOTICE_LEVEL


def test_more_seeds_starts_looping_the_seeds_it_widened(tmp_path: Path):
    """Widening the row starts the seed loop too — the point of a wider row is to
    cycle it, so the satellite plays the widened pool without a separate "loop
    seeds" after it."""
    config = _make_config(tmp_path)
    index, a, a2, b = _widened_loop_index(tmp_path)

    _set_current(config, 2, a)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        state, ops = dispatch_command("portrait_more_seeds", _make_state(), config)  # no loop running

    assert sorted(_playlist(config, 2)) == sorted([a, a2, b])   # looping the widened pool
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)
    assert state.portrait_loop == "seed"
    assert state.portrait_widen_clip == a
    assert [op.key for op in ops if op.op == "notice"] == ["More seeds"]


def test_more_seeds_during_a_seed_loop_widens_the_running_loop(tmp_path: Path):
    """Widening the row while a seed loop runs must widen the loop too, so the satellite
    cycles the wider pool the HUD now shows instead of only the exact family."""
    config = _make_config(tmp_path)
    index, a, a2, b = _widened_loop_index(tmp_path)
    state = _make_state(portrait_loop="seed")  # a seed loop is already running

    _set_current(config, 2, a)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        new_state, ops = dispatch_command("portrait_more_seeds", state, config)

    assert sorted(_playlist(config, 2)) == sorted([a, a2, b])  # re-looped wide
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)
    assert new_state.portrait_widen_clip == a
    assert new_state.portrait_loop == "seed"
    assert [op.key for op in ops if op.op == "notice"] == ["More seeds"]  # keeps its own notice


def test_portrait_cycle_seed_stays_within_the_current_action(tmp_path: Path):
    """Image-to-video clips of one source image share a seed family across
    actions (the family is keyed on the image, action-blind).  But the seed axis
    is "the same act, another subject", and the HUD draws only same-action members,
    so "next seed" must skip a different-action clip even though it is a sister
    seed — that clip is reached on the action axis instead."""
    config, paths = _make_grouped_config(tmp_path, {
        "a_alpha": _cycle_meta("200", "Alpha"),
        "b_alpha": _cycle_meta("300", "Alpha"),
        "c_gamma": _cycle_meta("250", "Gamma"),  # sister seed, other action
    })
    state = _make_state()
    # c sorts first above the current seed (200 < 250 < 300): without the action
    # gate the walk would land on it; with the gate it must reach b instead.

    _set_current(config, 2, paths["a_alpha"])
    _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    # b_alpha, not the sister-seed c_gamma (a different action, reached via the
    # action axis instead).
    assert _cmds(config, 2) == [f"PLAY_FILE {paths['b_alpha']}"]
    assert [op.key for op in ops if op.op == "notice"] == ["Next seed"]


def test_landscape_cycle_commands_target_the_landscape_player(tmp_path: Path):
    """The landscape variants must hit the landscape port and lock flag."""
    from fun_time.media_metadata import metadata_path_for, reset_group_index_cache

    reset_group_index_cache()
    config, paths = _make_grouped_config(tmp_path, {})
    media_root = config.regen_media_root
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
        sidecar = metadata_path_for(video, config.regen_metadata_root)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(meta), encoding="utf-8")
    config = replace(config, landscape_sources=str(landscape_dir))
    state = _make_state(locked3=True)

    _set_current(config, 3, videos["subject_zeta"])
    new_state, _ops = dispatch_command("landscape_cycle_action", state, config)

    assert new_state.locked3 is True, "cycling must not release the landscape lock"
    # Targets the landscape command file, keeping the lock (no UNLOCK queued)...
    assert _cmds(config, 3) == [f"PLAY_FILE {videos['subject_alpha']}"]
    assert _cmds(config, 2) == []  # ...and never touches the portrait side.


# --- latest / shuffle ---


def test_recents_stays_newest_first_and_resets_the_lock(tmp_path: Path):
    config = _make_config(tmp_path)
    config.regen_media_root = tmp_path / "media"
    config.regen_metadata_root = tmp_path / "metadata"
    # Already in Latest: asking again must keep newest-first, never toggle off.
    state = _make_state(portrait_latest=True, locked2=True)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        new_state, _ops = dispatch_command("portrait_latest", state, config)

    assert new_state.portrait_latest is True
    assert new_state.locked2 is False
    kwargs = mock_filter.call_args.kwargs
    assert kwargs["recent"] is True
    assert kwargs["f_mode_enabled"] is False
    assert kwargs["cmd_file"] == config.portrait_cmd_file
    # Latest must collapse action groups too, so the provider roots flow through.
    assert kwargs["regen_media_root"] == config.regen_media_root
    assert kwargs["regen_metadata_root"] == config.regen_metadata_root


# --- mode switch (genau_activate / nau_activate / hybrid_activate) ---


def test_nau_activate_deactivates_genau_and_raises_nau(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="genau")

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

    assert config.nau_cmd_file.read_text(encoding="utf-8").splitlines() == [
        "SET_HYBRID 0", "SET_TCODE_ENABLED 1",
    ]


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


def test_volume_down_steps_both_audio_sinks_down(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command("audio_volume_down", _make_state(), config)

    assert new_state.volume == 90
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_VOLUME 90"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "90"
    assert ops == [WindowOp(op="notice", key="Volume 90%", source="primary")]


def test_volume_up_steps_both_audio_sinks_up(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, _ops = dispatch_command("audio_volume_up", _make_state(volume=40), config)

    assert new_state.volume == 50
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_VOLUME 50"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "50"


def test_volume_clamps_at_silent_and_at_full(tmp_path: Path):
    config = _make_config(tmp_path)

    floored, _ops = dispatch_command("audio_volume_down", _make_state(volume=5), config)
    assert floored.volume == 0

    ceilinged, _ops = dispatch_command("audio_volume_up", _make_state(volume=95), config)
    assert ceilinged.volume == 100


def test_mute_silences_both_sinks_and_remembers_the_level(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command("audio_mute", _make_state(volume=70), config)

    assert new_state.muted is True
    assert new_state.volume == 70  # remembered, so unmuting restores it
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_VOLUME 0"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "0"
    assert ops == [WindowOp(op="notice", key="Muted", source="primary")]


def test_unmute_restores_the_level_the_mute_interrupted(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command(
        "audio_unmute", _make_state(volume=70, muted=True), config,
    )

    assert new_state.muted is False
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_VOLUME 70"
    assert ops == [WindowOp(op="notice", key="Volume 70%", source="primary")]


def test_mute_and_unmute_are_idempotent_not_a_toggle(tmp_path: Path):
    """Saying "mute" twice must not undo the mute — "un mute" is how you come back."""
    config = _make_config(tmp_path)

    still_muted, _ops = dispatch_command("audio_mute", _make_state(muted=True), config)
    assert still_muted.muted is True

    still_audible, _ops = dispatch_command("audio_unmute", _make_state(muted=False), config)
    assert still_audible.muted is False


def test_stepping_the_volume_lifts_a_mute(tmp_path: Path):
    """As reaching for the volume does in the Windows mixer — a "louder" that
    left the room silent would read as the command having been missed."""
    config = _make_config(tmp_path)

    new_state, _ops = dispatch_command(
        "audio_volume_up", _make_state(volume=70, muted=True), config,
    )

    assert new_state.muted is False
    assert new_state.volume == 80
    assert config.nau_cmd_file.read_text(encoding="utf-8") == "SET_VOLUME 80"


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

    new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert new_state.omni_paused is True
    assert any(op.op == "suspend_hotkeys" for op in ops)
    assert config.portrait_paused_file.read_text(encoding="utf-8") == "1"
    assert config.landscape_paused_file.read_text(encoding="utf-8") == "1"


def test_enter_omnipause_emits_disable_all_topmost(tmp_path: Path):
    """Entering omnipause must emit a disable_all_topmost op so the dispatch
    loop removes topmost from all windows."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert any(op.op == "disable_all_topmost" for op in ops)


def test_relief_omnipause_retracts_the_osr2_and_otherwise_enters_normally(tmp_path: Path):
    """Shift+Esc lands on the same frozen session a plain enter does, with the
    OSR2 sent away instead of home."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    new_state, ops = dispatch_command("relief_omnipause", state, config)

    assert new_state.omni_paused is True
    assert config.broker_cmd_file.read_text(encoding="utf-8") == "RETRACT"
    assert any(op.op == "disable_all_topmost" for op in ops)
    assert any(op.op == "suspend_hotkeys" for op in ops)
    assert config.portrait_paused_file.read_text(encoding="utf-8") == "1"
    assert config.landscape_paused_file.read_text(encoding="utf-8") == "1"


def test_omnipause_toggle_enter_emits_disable_all_topmost(tmp_path: Path):
    """Toggle entering omnipause must also emit disable_all_topmost."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert any(op.op == "disable_all_topmost" for op in ops)


def test_omnipause_toggle_leave_emits_restore_all_topmost(tmp_path: Path):
    """Leaving omnipause must emit restore_all_topmost so the dispatch
    loop rebuilds the z-order stack."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert any(op.op == "restore_all_topmost" for op in ops)


def test_leave_omnipause_emits_restore_all_topmost(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    new_state, ops = dispatch_command("leave_omnipause", state, config)

    assert any(op.op == "restore_all_topmost" for op in ops)


def test_enter_omnipause_does_not_remove_genau_topmost(tmp_path: Path):
    """Genau should stay topmost during omnipause — only pause playback."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False, primary_mode="genau")

    new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert not any(op.op == "hide_role" and op.key == "genau" for op in ops)


def test_omnipause_toggle_enter_does_not_remove_genau_topmost(tmp_path: Path):
    """Esc (omnipause toggle) should pause Genau, not remove its topmost."""
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False, primary_mode="genau")

    new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert not any(op.op == "hide_role" and op.key == "genau" for op in ops)


def test_leave_omnipause_resumes_satellites_only(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    new_state, ops = dispatch_command("leave_omnipause", state, config)

    assert new_state.omni_paused is False
    assert any(op.op == "unsuspend_hotkeys" for op in ops)
    assert config.portrait_paused_file.read_text(encoding="utf-8") == "0"
    assert config.landscape_paused_file.read_text(encoding="utf-8") == "0"


def test_leave_omnipause_adds_genau_ops_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True, primary_mode="genau")

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
    assert ops[0].op == "notice"
    assert ops[0].source == "primary"
    assert ops[0].key  # non-empty message


def test_clipper_save_no_notice_on_failure(tmp_path: Path):
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

    with patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

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

    with patch("fun_time.command_dispatch._clipper_python", return_value="python"), \
         patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 0
        mock_subprocess.run.return_value.stdout = "C:\\clipper\\sessions\\naustuff.json"
        mock_subprocess.run.return_value.stderr = ""
        new_state, ops = dispatch_command("clipper_save", state, config)

    cmd = mock_subprocess.run.call_args[0][0]
    assert "C:\\videos\\naustuff.mp4" in cmd
    assert "42.5" in cmd
    assert len(ops) == 1 and ops[0].op == "notice"


def test_clipper_save_in_nau_mode_skips_without_status(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(primary_mode="nau")

    with patch("fun_time.command_dispatch.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("clipper_save", state, config)

    mock_subprocess.run.assert_not_called()
    assert ops == []


# --- group loops and lock-action --------------------------------------------

def _loop_index(tmp_path: Path, *, axis: str) -> tuple[GroupIndex, str, str]:
    """A two-member group index on real files, keyed on the requested axis."""
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")
    a, b = str(first), str(second)
    ka, kb = normalize_path_key(a), normalize_path_key(b)
    if axis == "action":
        # Same subject, different acts.
        return GroupIndex(
            action_key_by_path={ka: "subject", kb: "subject"},
            action_members={"subject": [a, b]},
            action_by_path={ka: "Alpha", kb: "Kissing"},
            seed_key_by_path={}, seed_members={},
            path_by_key={ka: a, kb: b},
        ), a, b
    # Same act + params, different seeds.
    return GroupIndex(
        action_key_by_path={}, action_members={},
        action_by_path={ka: "Alpha", kb: "Alpha"},
        seed_key_by_path={ka: ("family", "1"), kb: ("family", "2")},
        seed_members={"family": [a, b]},
        path_by_key={ka: a, kb: b},
    ), a, b


def _widened_loop_index(tmp_path: Path) -> tuple[GroupIndex, str, str, str]:
    """Three same-scene clips {a, a2, b} on real files whose exact seed families
    split: a and a2 share the exact family F1, while b is its own render F2.  So
    `seed_family_members(a)` is {a, a2} but `widened_seed_members(a)` — b ranks in
    on its identical scene tags — is all three."""
    files = {name: tmp_path / f"{name}.mp4" for name in ("a", "a2", "b")}
    for f in files.values():
        f.write_text("x", encoding="utf-8")
    a, a2, b = (str(files["a"]), str(files["a2"]), str(files["b"]))
    ka, ka2, kb = normalize_path_key(a), normalize_path_key(a2), normalize_path_key(b)
    return GroupIndex(
        action_key_by_path={ka: "scene", ka2: "scene", kb: "scene"},
        action_members={"scene": sorted([a, a2, b])},
        action_by_path={ka: "Alpha", ka2: "Alpha", kb: "Alpha"},
        seed_key_by_path={ka: ("F1", "0"), ka2: ("F1", "1"), kb: ("F2", "0")},
        seed_members={"F1": sorted([a, a2]), "F2": [b]},
        path_by_key={ka: a, ka2: a2, kb: b},
        scene_tags_by_path={k: frozenset({"x", "y", "z"}) for k in (ka, ka2, kb)},
    ), a, a2, b


def test_action_loop_reshapes_the_queue_to_the_subjects_action_group(tmp_path: Path):
    config = _make_config(tmp_path)
    index, a, b = _loop_index(tmp_path, axis="action")

    _set_current(config, 2, a)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        _state, ops = dispatch_command("portrait_action_loop", _make_state(), config)

    # The group is written as the side's playlist (current-first) and reloaded; the
    # native player then loops it by auto-advance.
    assert sorted(_playlist(config, 2)) == sorted([a, b])
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)
    assert any(op.op == "notice" and op.source == "portrait" for op in ops)


def test_seed_loop_reshapes_the_queue_to_the_current_acts_seed_family(tmp_path: Path):
    config = _make_config(tmp_path)
    index, a, b = _loop_index(tmp_path, axis="seed")

    _set_current(config, 3, a)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        dispatch_command("landscape_seed_loop", _make_state(), config)

    assert sorted(_playlist(config, 3)) == sorted([a, b])
    assert "RELOAD_PLAYLIST" in _cmds(config, 3)


def test_a_widened_seed_loop_loops_the_loose_family_and_keeps_the_widen_anchor(tmp_path: Path):
    """When the row was widened around the clip on screen, "loop seeds" loops the
    whole loose family (across its exact seed families) and keeps the widen anchor in
    state, so the HUD stays widened and frozen as the loop auto-advances the re-renders."""
    config = _make_config(tmp_path)
    index, a, a2, b = _widened_loop_index(tmp_path)
    state = _make_state(portrait_widen_clip=a)  # widened around the clip on screen

    _set_current(config, 2, a)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        new_state, _ops = dispatch_command("portrait_seed_loop", state, config)

    assert sorted(_playlist(config, 2)) == sorted([a, a2, b])
    assert new_state.portrait_loop == "seed"
    assert new_state.portrait_widen_clip == a  # anchor kept so the HUD stays wide


def test_a_non_widened_seed_loop_clears_a_stale_widen_anchor(tmp_path: Path):
    """A plain "loop seeds" (widen anchor does not match the clip on screen) loops
    only the exact family, so it must drop any stale widen anchor — otherwise the
    HUD would wrongly read the exact-family loop as a widened one."""
    config = _make_config(tmp_path)
    index, a, b = _loop_index(tmp_path, axis="seed")
    state = _make_state(portrait_widen_clip="C:/somewhere/else.mp4")  # stale, not on screen

    _set_current(config, 2, a)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        new_state, _ops = dispatch_command("portrait_seed_loop", state, config)

    assert sorted(_playlist(config, 2)) == sorted([a, b])  # exact family only
    assert new_state.portrait_loop == "seed"
    assert new_state.portrait_widen_clip == ""  # stale anchor dropped


def test_loop_with_one_video_becomes_a_single_video_lock(tmp_path: Path):
    """A group of one is not a dead end: the loop buttons still work, they just
    mean "lock" then — repeat-one on the current clip, no sub-playlist."""
    config = _make_config(tmp_path)
    only = tmp_path / "only.mp4"
    only.write_text("x", encoding="utf-8")
    key = normalize_path_key(str(only))
    index = GroupIndex(
        action_key_by_path={key: "subject"}, action_members={"subject": [str(only)]},
        action_by_path={key: "Alpha"},
        seed_key_by_path={}, seed_members={},
        path_by_key={key: str(only)},
    )

    _set_current(config, 2, str(only))
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        new_state, ops = dispatch_command("portrait_action_loop", _make_state(), config)

    assert _playlist(config, 2) == []  # no queue reshape for a group of one
    assert _cmds(config, 2) == ["LOCK"]  # a group of one is really a single-video lock
    assert new_state.locked2 is True
    assert [op.key for op in ops if op.op == "notice"] == ["Locked"]


def test_action_loop_records_the_loop_axis_in_state(tmp_path: Path):
    """The HUD reads this flag to freeze its map on the looped group and keep the
    loop button lit while the clip auto-advances, so it must live in the state."""
    config = _make_config(tmp_path)
    index, a, _b = _loop_index(tmp_path, axis="action")

    _set_current(config, 2, a)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        state, _ops = dispatch_command("portrait_action_loop", _make_state(), config)

    assert state.portrait_loop == "action"
    assert state.landscape_loop == ""


def test_seed_loop_records_the_loop_axis_in_state(tmp_path: Path):
    config = _make_config(tmp_path)
    index, a, _b = _loop_index(tmp_path, axis="seed")

    _set_current(config, 3, a)
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        state, _ops = dispatch_command("landscape_seed_loop", _make_state(), config)

    assert state.landscape_loop == "seed"
    assert state.portrait_loop == ""


def test_a_loop_anchors_on_the_clip_it_started_on(tmp_path: Path):
    """A loop's queue is written clip-on-screen-first, so the HUD has to order its
    map the same way — from the clip the loop started on.

    Anchoring the map on some other member (the group's lowest-keyed one) drew the
    clip on screen somewhere in the middle of the row the instant the loop began,
    and made the action column light up bottom-to-top as the group played.
    """
    config = _make_config(tmp_path)
    index, a, b = _loop_index(tmp_path, axis="seed")

    _set_current(config, 2, b)  # the loop starts on the group's *second* member
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        state, _ops = dispatch_command("portrait_seed_loop", _make_state(), config)

    assert state.portrait_map_anchor == b
    assert _playlist(config, 2) == [b, a]  # the map's order is the queue's order


def test_single_video_lock_clears_a_prior_loop(tmp_path: Path):
    """The one-member "loop" is really a lock, so it must drop any loop the side
    was running instead of leaving a stale flag."""
    config = _make_config(tmp_path)
    only = tmp_path / "only.mp4"
    only.write_text("x", encoding="utf-8")
    key = normalize_path_key(str(only))
    index = GroupIndex(
        action_key_by_path={key: "subject"}, action_members={"subject": [str(only)]},
        action_by_path={key: "Alpha"},
        seed_key_by_path={}, seed_members={},
        path_by_key={key: str(only)},
    )

    _set_current(config, 2, str(only))
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        state, _ops = dispatch_command(
            "portrait_action_loop",
            _make_state(portrait_loop="seed", portrait_widen_clip=str(only)), config,
        )

    assert state.portrait_loop == ""
    assert state.portrait_widen_clip == ""  # the lock drops the widened row too


def test_toggling_the_lock_ends_a_loop(tmp_path: Path):
    """A lock is repeat-one on one clip — incompatible with a loop's repeat-all —
    so locking clears the loop flag and the widened row that rode on it."""
    config = _make_config(tmp_path)

    _set_current(config, 2, r"C:\videos\provider2\abc_123.mp4")
    with patch("fun_time.command_dispatch.ensure_in_favs"):
        state, _ops = dispatch_command(
            "portrait_lock",
            _make_state(locked2=False, portrait_loop="action", portrait_widen_clip=r"C:\videos\provider2\abc_123.mp4"),
            config,
        )

    assert state.portrait_loop == ""
    assert state.portrait_widen_clip == ""


def test_an_applied_filter_clears_a_running_loop(tmp_path: Path):
    """A filter that selects videos rebuilds the playlist, replacing the loop's
    sub-playlist — so the loop (and any widened row) is gone."""
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        state, _ops = dispatch_command(
            "filter_portrait_alpha",
            _make_state(portrait_loop="seed", portrait_widen_clip="C:/v/anchor.mp4"), config,
        )

    assert state.portrait_loop == ""
    assert state.portrait_widen_clip == ""


def test_a_zero_match_filter_leaves_a_running_loop_alone(tmp_path: Path):
    """A filter that matches nothing never touches the playlist, so a loop that
    was running — and its widened row — survives it."""
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=False)
        state, _ops = dispatch_command(
            "filter_portrait_alpha",
            _make_state(portrait_loop="seed", portrait_widen_clip="C:/v/anchor.mp4"), config,
        )

    assert state.portrait_loop == "seed"
    assert state.portrait_widen_clip == "C:/v/anchor.mp4"


def test_no_loop_clears_the_loop_but_leaves_the_map_where_it_hangs(tmp_path: Path):
    """Ending a loop must take away the loop's own chrome — the lit button and the
    rectangle — and nothing else.

    The map keeps hanging on the same clip, over the same (possibly widened) row, so
    the thumbnails do not re-home onto whichever member the loop had reached; it lets
    go by itself once the browse moves on past the group.
    """
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.satellite_browse_paths", return_value=["C:/v/x.mp4"]):
        state, _ops = dispatch_command(
            "portrait_no_loop",
            _make_state(portrait_loop="action", portrait_map_anchor="C:/v/anchor.mp4",
                        portrait_widen_clip="C:/v/anchor.mp4"), config,
        )

    assert state.portrait_loop == ""                            # the loop is off
    assert state.portrait_map_anchor == "C:/v/anchor.mp4"       # the map stays put
    assert state.portrait_widen_clip == "C:/v/anchor.mp4"       # …and stays widened


def test_no_loop_reshapes_the_queue_to_the_browse_in_place(tmp_path: Path):
    """Cancelling a loop must not yank you to a different clip: the queue is
    reshaped to the browse in place (retarget keeps the current clip playing),
    never replaced — so no restart, no seek-back papering over one."""
    config = _make_config(tmp_path)
    browse = ["C:/v/one.mp4", "C:/v/two.mp4"]

    with patch("fun_time.command_dispatch.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", _make_state(), config)

    # The browse is written as the side's playlist and reloaded in place.
    assert _playlist(config, 2) == browse
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)


def test_no_loop_keeps_the_clip_on_screen_by_heading_the_restored_browse(tmp_path: Path):
    """Turning a loop OFF must not interrupt the clip playing.

    The player keeps its clip across a reload only when that clip is still in the
    new list, and a loop member usually is NOT in the browse — the browse holds one
    representative per group, and the loop was cycling the others.  Without this the
    reload fell through to "restart at the top" and yanked the user onto another
    video, which is exactly the interruption the loop-off toggle must not cause.
    """
    config = _make_config(tmp_path)
    playing = "C:/v/seed_4.mp4"  # a loop member, not one of the browse's picks
    browse = ["C:/v/one.mp4", "C:/v/two.mp4"]
    _set_current(config, 2, playing)

    with patch("fun_time.command_dispatch.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", _make_state(portrait_loop="seed"), config)

    # It heads the restored list, so the reload keeps it playing and the browse is
    # simply what comes up next.
    assert _playlist(config, 2) == [playing, *browse]


def test_no_loop_leaves_a_browse_that_already_holds_the_clip_untouched(tmp_path: Path):
    """When the clip on screen IS one of the browse's picks the reload already keeps
    it, so the browse keeps its own order — no needless reshuffle of what comes next."""
    config = _make_config(tmp_path)
    browse = ["C:/v/one.mp4", "C:/v/two.mp4", "C:/v/three.mp4"]
    _set_current(config, 2, "C:/v/two.mp4")

    with patch("fun_time.command_dispatch.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", _make_state(portrait_loop="seed"), config)

    assert _playlist(config, 2) == browse


def test_no_loop_leaves_the_queue_alone_when_the_browse_is_empty(tmp_path: Path):
    """A filter that now matches nothing must not blank the queue: with no browse
    paths, no_loop only clears the flag and never reshapes the live queue."""
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.satellite_browse_paths", return_value=[]):
        state, ops = dispatch_command("portrait_no_loop", _make_state(portrait_loop="seed"), config)

    assert _playlist(config, 2) == []  # empty browse never blanks the live queue
    assert "RELOAD_PLAYLIST" not in _cmds(config, 2)
    assert state.portrait_loop == ""
    assert [op.key for op in ops if op.op == "notice"] == ["Loop off"]


def test_a_reorder_clears_only_its_own_sides_loop(tmp_path: Path):
    """A reorder rebuilds the side it names, so that side's loop goes — and the
    other side, which was not rebuilt, keeps looping."""
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        state, _ops = dispatch_command(
            "portrait_latest",
            _make_state(portrait_loop="seed", landscape_loop="action",
                        portrait_widen_clip="C:/v/p.mp4", landscape_widen_clip="C:/v/l.mp4"),
            config,
        )

    assert (state.portrait_loop, state.portrait_widen_clip) == ("", "")
    assert (state.landscape_loop, state.landscape_widen_clip) == ("action", "C:/v/l.mp4")


def test_fmode_toggle_clears_both_loops(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.command_dispatch.apply_toggle_fmode") as mock_fmode:
        mock_fmode.return_value = type(
            "R", (), {"next_f_mode_enabled": True, "next_locked2": False,
                      "next_locked3": False, "log_message": ""}
        )()
        state, _ops = dispatch_command(
            "fmode_toggle",
            _make_state(portrait_loop="seed", landscape_loop="action",
                        portrait_widen_clip="C:/v/p.mp4", landscape_widen_clip="C:/v/l.mp4"),
            config,
        )

    assert (state.portrait_loop, state.landscape_loop) == ("", "")
    assert (state.portrait_widen_clip, state.landscape_widen_clip) == ("", "")


def test_lock_action_filters_to_the_current_clips_action(tmp_path: Path):
    config = _make_config(tmp_path)

    _set_current(config, 2, "C:/v/clip.mp4")
    with patch("fun_time.command_dispatch._video_action_label", return_value="Beta Gamma"), \
         patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        new_state, _ops = dispatch_command("portrait_lock_action", _make_state(), config)

    assert mock_filter.call_args.kwargs["query"] == "beta gamma"
    assert new_state.portrait_filter == "beta gamma"
    assert new_state.landscape_filter == ""


def test_action_loop_groups_the_video_that_was_playing_when_spoken(tmp_path: Path):
    """A group command names the clip the speaker had in front of them, so the
    group is that clip's — not that of whatever the satellite advanced to."""
    config = _make_config(tmp_path)
    index, meant, sibling = _loop_index(tmp_path, axis="action")

    _set_current(config, 2, "C:/v/advanced_to.mp4")
    with patch("fun_time.command_dispatch._satellite_group_index", return_value=index):
        dispatch_command("portrait_action_loop", _make_state(), config, target_path=meant)

    # The group is the back-dated (spoken) clip's, not the newcomer's.
    assert sorted(_playlist(config, 2)) == sorted([meant, sibling])


def test_lock_action_filters_to_the_action_of_the_video_playing_when_spoken(tmp_path: Path):
    config = _make_config(tmp_path)
    meant = "C:/v/meant.mp4"
    labelled: list[str] = []

    _set_current(config, 2, "C:/v/advanced_to.mp4")
    with patch("fun_time.command_dispatch._video_action_label",
               side_effect=lambda path, _config: labelled.append(path) or "Beta Gamma"), \
         patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        dispatch_command("portrait_lock_action", _make_state(), config, target_path=meant)

    assert labelled == [meant]
    assert mock_filter.call_args.kwargs["query"] == "beta gamma"


def test_lock_action_without_metadata_says_so(tmp_path: Path):
    config = _make_config(tmp_path)

    _set_current(config, 3, "C:/v/clip.mp4")
    with patch("fun_time.command_dispatch._video_action_label", return_value=""), \
         patch("fun_time.command_dispatch.apply_satellite_filter") as mock_filter:
        new_state, ops = dispatch_command("landscape_lock_action", _make_state(), config)

    mock_filter.assert_not_called()
    assert new_state.landscape_filter == ""
    assert any(
        op.op == "notice" and "No action metadata" in op.key and op.source == "landscape"
        for op in ops
    )
