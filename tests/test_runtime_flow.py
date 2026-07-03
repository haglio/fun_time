from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.runtime_flow import (
    apply_enter_omnipause,
    apply_leave_omnipause,
    apply_mode_switch,
    apply_toggle_fmode,
    build_omnipause_toggle,
)


@pytest.fixture
def flow_files(tmp_path: Path) -> dict[str, Path]:
    return {
        "genau_paused_file": tmp_path / "genau_paused.txt",
        "audio_paused_file": tmp_path / "audio_paused.txt",
        "genau_cmd_file": tmp_path / "genau_cmd.txt",
        "nau_paused_file": tmp_path / "nau_paused.txt",
        "broker_cmd_file": tmp_path / "broker_cmd.txt",
    }


def _mode_switch(files, monkeypatch, calls, *, current, target, omni_paused=False, broker=False):
    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )
    return apply_mode_switch(
        current_mode=current,
        target_mode=target,
        omni_paused=omni_paused,
        genau_paused_file=files["genau_paused_file"],
        audio_paused_file=files["audio_paused_file"],
        genau_cmd_file=files["genau_cmd_file"],
        nau_paused_file=files["nau_paused_file"],
        primary_port=8123,
        password="pw",
        broker_cmd_file=files["broker_cmd_file"] if broker else None,
    )


def test_nau_to_genau_resumes_genau_and_pauses_nau(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    result = _mode_switch(flow_files, monkeypatch, calls, current="nau", target="genau")

    assert result.next_mode == "genau"
    assert result.is_transition is True
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "1"
    assert calls == [], "Primary VLC stays paused outside hybrid"


def test_genau_to_nau_pauses_genau_and_resumes_nau(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    result = _mode_switch(flow_files, monkeypatch, calls, current="genau", target="nau")

    assert result.next_mode == "nau"
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "PAUSE"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "0"
    assert calls == []


def test_nau_to_hybrid_starts_vlc_and_pauses_nau(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    result = _mode_switch(flow_files, monkeypatch, calls, current="nau", target="hybrid")

    assert result.is_transition is True
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME\nHUD_ON"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "1"
    assert calls == [(8123, "pw", True)]


def test_hybrid_to_nau_stops_vlc_and_resumes_nau(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    result = _mode_switch(flow_files, monkeypatch, calls, current="hybrid", target="nau")

    assert result.next_mode == "nau"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "PAUSE\nHUD_OFF"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "0"
    assert calls == [(8123, "pw", False)]


def test_hybrid_to_genau_stops_vlc_and_leaves_nau_paused(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    result = _mode_switch(flow_files, monkeypatch, calls, current="hybrid", target="genau")

    assert result.next_mode == "genau"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "HUD_OFF"
    assert not flow_files["nau_paused_file"].exists(), "Nau pause state untouched"
    assert calls == [(8123, "pw", False)]


def test_genau_to_hybrid_starts_vlc_and_leaves_nau_paused(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    result = _mode_switch(flow_files, monkeypatch, calls, current="genau", target="hybrid")

    assert result.next_mode == "hybrid"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "HUD_ON"
    assert not flow_files["nau_paused_file"].exists()
    assert calls == [(8123, "pw", True)]


def test_mode_switch_during_omnipause_no_side_effects(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    result = _mode_switch(
        flow_files, monkeypatch, calls, current="nau", target="genau", omni_paused=True,
    )

    assert result.next_mode == "genau"
    assert result.is_transition is False
    assert calls == [], "Omnipause must NOT call ensure_playback_state"
    assert not flow_files["genau_paused_file"].exists(), "Omnipause must NOT write flag files"
    assert not flow_files["nau_paused_file"].exists()
    assert not flow_files["genau_cmd_file"].exists(), "Omnipause must NOT write cmd file"


def test_genau_to_nau_writes_broker_resume(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    _mode_switch(flow_files, monkeypatch, calls, current="genau", target="nau", broker=True)

    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "RESUME"


def test_hybrid_to_nau_writes_broker_resume(monkeypatch, flow_files):
    """Leaving genau-active hybrid for nau must un-PARK the broker —
    previously only the genau->primary handoff had this coverage."""
    calls: list[tuple[int, str, bool]] = []

    _mode_switch(flow_files, monkeypatch, calls, current="hybrid", target="nau", broker=True)

    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "RESUME"


def test_nau_to_genau_does_not_write_broker_cmd(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    _mode_switch(flow_files, monkeypatch, calls, current="nau", target="genau", broker=True)

    assert not flow_files["broker_cmd_file"].exists(), "Activation must not write to broker"


def test_toggle_fmode_replaces_playlists_and_reloads_nau(monkeypatch, tmp_path: Path):
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
    nau_cmd_file = tmp_path / "nau_cmd.txt"
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
        nau_cmd_file=nau_cmd_file,
    )

    assert result.success is True
    assert result.next_f_mode_enabled is True
    assert result.next_locked2 is False
    assert result.next_locked3 is False
    assert [call[0] for call in playlist_calls] == [9001, 9002, 9003]
    assert playlist_calls[1][3] == "all"
    assert playlist_calls[2][3] == "all"
    assert nau_cmd_file.read_text(encoding="utf-8") == "RELOAD_PLAYLIST"
    assert (tmp_path / "state" / "nau_playlist.tsv").exists()


def test_build_omnipause_toggle_returns_enter_or_leave():
    enter = build_omnipause_toggle(omni_paused=False, primary_mode="nau")
    leave = build_omnipause_toggle(omni_paused=True, primary_mode="genau")

    assert enter.action == "enter"
    assert enter.next_omni_paused is True
    assert leave.action == "leave"
    assert leave.next_omni_paused is False


def test_apply_enter_omnipause_pauses_everything(monkeypatch, flow_files):
    calls: list[tuple[int, str, bool]] = []

    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )

    result = apply_enter_omnipause(
        omni_paused=False,
        primary_mode="genau",
        portrait_port=9002,
        landscape_port=9003,
        primary_port=9001,
        password="pw",
        genau_paused_file=flow_files["genau_paused_file"],
        audio_paused_file=flow_files["audio_paused_file"],
        genau_cmd_file=flow_files["genau_cmd_file"],
        nau_paused_file=flow_files["nau_paused_file"],
        broker_cmd_file=flow_files["broker_cmd_file"],
    )

    assert result.action == "enter"
    assert result.next_omni_paused is True
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "PAUSE"
    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "PARK"
    # Calls happen in parallel, so order is non-deterministic
    assert sorted(calls) == sorted([(9002, "pw", False), (9003, "pw", False), (9001, "pw", False)])


def _leave_omnipause(files, monkeypatch, calls, *, primary_mode, skip_primary_resume=False, broker=True):
    monkeypatch.setattr(
        "fun_time.runtime_flow.ensure_playback_state",
        lambda port, password, should_play: calls.append((port, password, should_play)) or True,
    )
    return apply_leave_omnipause(
        omni_paused=True,
        primary_mode=primary_mode,
        skip_primary_resume=skip_primary_resume,
        primary_port=9001,
        portrait_port=9002,
        landscape_port=9003,
        password="pw",
        genau_paused_file=files["genau_paused_file"],
        audio_paused_file=files["audio_paused_file"],
        genau_cmd_file=files["genau_cmd_file"],
        nau_paused_file=files["nau_paused_file"],
        broker_cmd_file=files["broker_cmd_file"] if broker else None,
    )


def test_apply_leave_omnipause_in_nau_mode_resumes_nau_not_vlc(monkeypatch, flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["nau_paused_file"].write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    result = _leave_omnipause(flow_files, monkeypatch, calls, primary_mode="nau")

    assert result.action == "leave"
    assert result.next_omni_paused is False
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "0"
    # Genau stays paused when primary_mode is nau
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "1"
    assert not flow_files["genau_cmd_file"].exists()
    # Broker is un-PARKed regardless of mode
    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "RESUME"
    # Only the satellites resume — primary VLC plays only in hybrid
    assert sorted(calls) == sorted([(9002, "pw", True), (9003, "pw", True)])


def test_apply_leave_omnipause_in_hybrid_mode_resumes_vlc_and_genau(monkeypatch, flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["audio_paused_file"].write_text("1", encoding="utf-8")
    flow_files["nau_paused_file"].write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    _leave_omnipause(flow_files, monkeypatch, calls, primary_mode="hybrid")

    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "1", "Nau stays paused"
    assert sorted(calls) == sorted([(9001, "pw", True), (9002, "pw", True), (9003, "pw", True)])


def test_apply_leave_omnipause_in_genau_mode_resumes_genau_only(monkeypatch, flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["audio_paused_file"].write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    result = _leave_omnipause(flow_files, monkeypatch, calls, primary_mode="genau", broker=False)

    assert result.action == "leave"
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME"
    assert not flow_files["nau_paused_file"].exists(), "Nau pause state untouched"
    assert sorted(calls) == sorted([(9002, "pw", True), (9003, "pw", True)])


def test_apply_leave_omnipause_skip_primary_still_resumes_nau(monkeypatch, flow_files):
    """After the Nau file dialog, skip_primary_resume must not keep Nau paused."""
    flow_files["nau_paused_file"].write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    _leave_omnipause(
        flow_files, monkeypatch, calls, primary_mode="nau", skip_primary_resume=True,
    )

    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "0"
    assert sorted(calls) == sorted([(9002, "pw", True), (9003, "pw", True)])


def test_apply_leave_omnipause_skip_primary_in_hybrid_skips_vlc(monkeypatch, flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    calls: list[tuple[int, str, bool]] = []

    _leave_omnipause(
        flow_files, monkeypatch, calls, primary_mode="hybrid", skip_primary_resume=True,
    )

    assert sorted(calls) == sorted([(9002, "pw", True), (9003, "pw", True)])
