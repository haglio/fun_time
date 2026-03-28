from __future__ import annotations

from pathlib import Path

from fun_time.runtime_flow import (
    apply_enter_omnipause,
    apply_leave_omnipause,
    apply_sync_robot_hand,
    apply_toggle_fmode,
    build_omnipause_toggle,
)


def test_sync_entering_transition_pauses_primary_and_writes_files(monkeypatch, tmp_path: Path):
    enabled_file = tmp_path / "enabled.txt"
    mode_state_file = tmp_path / "mode.txt"
    paused_file = tmp_path / "paused.txt"
    audio_paused_file = tmp_path / "audio_paused.txt"
    enabled_file.write_text("1", encoding="utf-8")
    mode_state_file.write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_sync_robot_hand(
        robot_hand_mode_on=False,
        omni_paused=False,
        enabled_file=enabled_file,
        mode_state_file=mode_state_file,
        paused_file=paused_file,
        audio_paused_file=audio_paused_file,
        primary_port=8123,
        password="pw",
    )

    assert result.next_robot_hand_mode is True
    assert result.is_transition is True
    assert paused_file.read_text(encoding="utf-8") == "0"
    assert audio_paused_file.read_text(encoding="utf-8") == "0"
    assert calls == [(8123, "pw", False)]


def test_sync_leaving_transition_resumes_primary_and_writes_files(monkeypatch, tmp_path: Path):
    enabled_file = tmp_path / "enabled.txt"
    mode_state_file = tmp_path / "mode.txt"
    paused_file = tmp_path / "paused.txt"
    audio_paused_file = tmp_path / "audio_paused.txt"
    enabled_file.write_text("1", encoding="utf-8")
    mode_state_file.write_text("0", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_sync_robot_hand(
        robot_hand_mode_on=True,
        omni_paused=False,
        enabled_file=enabled_file,
        mode_state_file=mode_state_file,
        paused_file=paused_file,
        audio_paused_file=audio_paused_file,
        primary_port=8123,
        password="pw",
    )

    assert result.next_robot_hand_mode is False
    assert result.is_transition is True
    assert paused_file.read_text(encoding="utf-8") == "1"
    assert audio_paused_file.read_text(encoding="utf-8") == "1"
    assert calls == [(8123, "pw", True)]


def test_sync_steady_state_does_not_call_vlc(monkeypatch, tmp_path: Path):
    """THE KEY REGRESSION TEST: steady state must not call ensure_playback_state."""
    enabled_file = tmp_path / "enabled.txt"
    mode_state_file = tmp_path / "mode.txt"
    paused_file = tmp_path / "paused.txt"
    audio_paused_file = tmp_path / "audio_paused.txt"
    enabled_file.write_text("1", encoding="utf-8")
    mode_state_file.write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_sync_robot_hand(
        robot_hand_mode_on=True,
        omni_paused=False,
        enabled_file=enabled_file,
        mode_state_file=mode_state_file,
        paused_file=paused_file,
        audio_paused_file=audio_paused_file,
        primary_port=8123,
        password="pw",
    )

    assert result.next_robot_hand_mode is True
    assert result.is_transition is False
    assert calls == [], "Steady state must NOT call ensure_playback_state"
    assert not paused_file.exists(), "Steady state must NOT write flag files"


def test_toggle_disabling_resumes_primary(monkeypatch, tmp_path: Path):
    from fun_time.runtime_flow import apply_toggle_robot_hand_enabled

    enabled_file = tmp_path / "enabled.txt"
    mode_state_file = tmp_path / "mode.txt"
    paused_file = tmp_path / "paused.txt"
    audio_paused_file = tmp_path / "audio_paused.txt"
    enabled_file.write_text("1", encoding="utf-8")
    mode_state_file.write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_toggle_robot_hand_enabled(
        robot_hand_mode_on=True,
        omni_paused=False,
        enabled_file=enabled_file,
        mode_state_file=mode_state_file,
        paused_file=paused_file,
        audio_paused_file=audio_paused_file,
        primary_port=8123,
        password="pw",
    )

    assert result.next_robot_hand_mode is False
    assert result.is_transition is True
    assert enabled_file.read_text(encoding="utf-8") == "0"
    assert calls == [(8123, "pw", True)]


def test_toggle_enabling_pauses_primary_when_auto_on(monkeypatch, tmp_path: Path):
    from fun_time.runtime_flow import apply_toggle_robot_hand_enabled

    enabled_file = tmp_path / "enabled.txt"
    mode_state_file = tmp_path / "mode.txt"
    paused_file = tmp_path / "paused.txt"
    audio_paused_file = tmp_path / "audio_paused.txt"
    enabled_file.write_text("0", encoding="utf-8")
    mode_state_file.write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_toggle_robot_hand_enabled(
        robot_hand_mode_on=False,
        omni_paused=False,
        enabled_file=enabled_file,
        mode_state_file=mode_state_file,
        paused_file=paused_file,
        audio_paused_file=audio_paused_file,
        primary_port=8123,
        password="pw",
    )

    assert result.next_robot_hand_mode is True
    assert result.is_transition is True
    assert enabled_file.read_text(encoding="utf-8") == "1"
    assert calls == [(8123, "pw", False)]


def test_toggle_fmode_replaces_playlists_and_returns_new_state(monkeypatch, tmp_path: Path):
    primary_root = tmp_path / "videos" / "videos" / "primary"
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    for root in (primary_root, portrait_root, landscape_root):
        root.mkdir(parents=True)
    primary_video = primary_root / "main.mp4"
    portrait_video = portrait_root / "portrait.mp4"
    landscape_video = landscape_root / "landscape.mp4"
    for path in (primary_video, portrait_video, landscape_video):
        path.write_text("x", encoding="utf-8")
    mirrored = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "main.funscript"
    mirrored.parent.mkdir(parents=True, exist_ok=True)
    mirrored.write_text("{}", encoding="utf-8")
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text(
        f'local_file,web_url\r\n"x","{portrait_video}"\r\n"x","{landscape_video}"\r\n',
        encoding="utf-8",
    )
    playlist_calls: list[tuple[int, str, str, str]] = []

    def fake_replace(port, password, playlist_path, repeat_mode=""):
        playlist_calls.append((port, password, str(playlist_path), repeat_mode))
        return True

    monkeypatch.setattr("fun_time.runtime_flow.replace_playlist_from_file", fake_replace)

    result = apply_toggle_fmode(
        f_mode_enabled=False,
        primary_sources=str(primary_root),
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=favs_file,
        state_dir=tmp_path / "state",
        primary_port=9001,
        portrait_port=9002,
        landscape_port=9003,
        password="pw",
    )

    assert result.success is True
    assert result.next_f_mode_enabled is True
    assert result.next_locked2 is False
    assert result.next_locked3 is False
    assert [call[0] for call in playlist_calls] == [9001, 9002, 9003]
    assert playlist_calls[1][3] == "all"
    assert playlist_calls[2][3] == "all"


def test_build_omnipause_toggle_returns_enter_or_leave():
    enter = build_omnipause_toggle(omni_paused=False, robot_hand_mode_on=False)
    leave = build_omnipause_toggle(omni_paused=True, robot_hand_mode_on=True)

    assert enter.action == "enter"
    assert enter.next_omni_paused is True
    assert leave.action == "leave"
    assert leave.next_omni_paused is False
    assert leave.robot_hand_branch is True


def test_apply_enter_omnipause_pauses_satellites_and_marks_pause_files(monkeypatch, tmp_path: Path):
    paused_file = tmp_path / "robot_paused.txt"
    audio_paused_file = tmp_path / "audio_paused.txt"
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_enter_omnipause(
        omni_paused=False,
        robot_hand_mode_on=True,
        portrait_port=9002,
        landscape_port=9003,
        primary_port=9001,
        password="pw",
        robot_hand_paused_file=paused_file,
        audio_paused_file=audio_paused_file,
    )

    assert result.action == "enter"
    assert result.next_omni_paused is True
    assert paused_file.read_text(encoding="utf-8") == "1"
    assert audio_paused_file.read_text(encoding="utf-8") == "1"
    # Calls happen in parallel, so order is non-deterministic
    assert sorted(calls) == sorted([(9002, "pw", False), (9003, "pw", False), (9001, "pw", False)])


def test_apply_leave_omnipause_resumes_satellites_and_primary(monkeypatch, tmp_path: Path):
    paused_file = tmp_path / "robot_paused.txt"
    audio_paused_file = tmp_path / "audio_paused.txt"
    paused_file.write_text("1", encoding="utf-8")
    audio_paused_file.write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_leave_omnipause(
        omni_paused=True,
        robot_hand_mode_on=False,
        skip_primary_resume=False,
        primary_port=9001,
        portrait_port=9002,
        landscape_port=9003,
        password="pw",
        robot_hand_paused_file=paused_file,
        audio_paused_file=audio_paused_file,
    )

    assert result.action == "leave"
    assert result.next_omni_paused is False
    assert paused_file.read_text(encoding="utf-8") == "0"
    assert audio_paused_file.read_text(encoding="utf-8") == "0"
    # Calls happen in parallel, so order is non-deterministic
    assert sorted(calls) == sorted([(9002, "pw", True), (9003, "pw", True), (9001, "pw", True)])


def test_apply_leave_omnipause_resumes_satellites_even_when_primary_skipped(monkeypatch, tmp_path: Path):
    paused_file = tmp_path / "robot_paused.txt"
    audio_paused_file = tmp_path / "audio_paused.txt"
    paused_file.write_text("1", encoding="utf-8")
    audio_paused_file.write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_leave_omnipause(
        omni_paused=True,
        robot_hand_mode_on=True,
        skip_primary_resume=False,
        primary_port=9001,
        portrait_port=9002,
        landscape_port=9003,
        password="pw",
        robot_hand_paused_file=paused_file,
        audio_paused_file=audio_paused_file,
    )

    assert result.action == "leave"
    assert result.next_omni_paused is False
    # Calls happen in parallel, so order is non-deterministic
    assert sorted(calls) == sorted([(9002, "pw", True), (9003, "pw", True)])
