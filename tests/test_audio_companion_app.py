from __future__ import annotations

import logging
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fun_time import audio_companion_app
from fun_time.audio_companion_app import AudioPlaybackController, find_audio


class TestFindAudio:
    def test_returns_first_supported_extension(self, tmp_path: Path):
        mp3 = tmp_path / "demo.mp3"
        wav = tmp_path / "demo.wav"
        mp3.write_bytes(b"mp3")
        wav.write_bytes(b"wav")

        assert find_audio(tmp_path, "demo") == mp3

    def test_returns_none_when_stem_missing(self, tmp_path: Path):
        assert find_audio(tmp_path, "missing") is None


class TestTheFlagFilesThisProcessReads:
    """Both are read through `player_core.file_channel.read_paused_state`, which
    every other player in the family already reads its own with.  This module
    carried two byte-identical copies of that body, differing only in name."""

    @pytest.mark.parametrize("text, expected", [
        ("1", True), ("0", False), ("", False), ("true", False),
        (" 1 \n", True), ("\ufeff1", True),
    ])
    def test_only_an_exact_1_is_true(self, tmp_path: Path, text: str, expected: bool):
        from player_core.file_channel import read_paused_state

        path = tmp_path / "flag.txt"
        path.write_text(text, encoding="utf-8")

        assert read_paused_state(path) is expected

    def test_a_file_that_is_not_there_yet_is_not_true(self, tmp_path: Path):
        from player_core.file_channel import read_paused_state

        assert read_paused_state(tmp_path / "never_written.txt") is False

    def test_the_runtime_is_given_one_reader_for_both_flags(self, tmp_path, cfg_path):
        """One predicate for both files.  The VR orchestrator writes one of them
        and a player reads it back, so the two ends must not be able to
        disagree about what a value means."""
        from fun_time import audio_companion_app

        (tmp_path / "audio").mkdir(exist_ok=True)
        with patch.object(audio_companion_app.pygame.mixer, "init"), \
             patch.object(audio_companion_app, "AudioCompanionRuntime") as runtime, \
             patch("socket.socket"):
            runtime.return_value.run_forever.side_effect = KeyboardInterrupt
            with pytest.raises(KeyboardInterrupt):
                audio_companion_app.main(
                    ["--config", str(cfg_path), "--audio-folder", str(tmp_path / "audio")])

        given = runtime.call_args.kwargs
        assert given["read_mode_active"] is given["read_paused_state"]

    def test_no_local_copy_of_that_reader_is_left(self):
        from fun_time import audio_companion_app

        assert not hasattr(audio_companion_app, "read_mode_active")


class _FakeMusic:
    """The pygame music channel with a memory: what was loaded, whether it is
    playing, where each play started, and the level it was set to.  The one
    thing these tests stub — everything the controller itself does (position
    arithmetic, pause bookkeeping, the visibility/mode gate) runs for real."""

    def __init__(self):
        self.loaded: str | None = None
        self.busy = False
        self.play_starts: list[float] = []
        self.events: list[str] = []
        self.volume: float | None = None

    def load(self, path):
        # Like pygame: loading a new stream stops whatever was playing.
        self.loaded = path
        self.busy = False
        self.events.append("load")

    def play(self, loops=0, start=0.0):
        self.busy = True
        self.play_starts.append(start)
        self.events.append("play")

    def pause(self):
        self.events.append("pause")

    def unpause(self):
        self.events.append("unpause")

    def stop(self):
        self.busy = False
        self.events.append("stop")

    def get_busy(self):
        return self.busy

    def set_volume(self, value):
        self.volume = value
        self.events.append("volume")

    def set_pos(self, value):
        self.events.append("set_pos")


class TestAudioPlaybackController:
    @staticmethod
    def _make_controller(tmp_path: Path, music=None, **overrides):
        return AudioPlaybackController(
            audio_folder=tmp_path,
            logger=logging.getLogger("test.audio"),
            music=music if music is not None else _FakeMusic(),
            sound_length=lambda _path: None,
            **overrides,
        )

    def _running(self, tmp_path: Path, monkeypatch):
        """A controller in genau mode with a real clip file, a remembering
        mixer, and a hand-turned clock."""
        music = _FakeMusic()
        clock = {"now": 100.0}
        monkeypatch.setattr(
            audio_companion_app, "time",
            types.SimpleNamespace(monotonic=lambda: clock["now"]),
        )
        clip = tmp_path / "demo.mp3"
        clip.write_bytes(b"demo")
        controller = self._make_controller(tmp_path, music)
        controller.mode_active = True
        controller.visible = True
        return controller, music, clock, clip

    def test_hiding_pauses_and_remembers_where_the_clip_was(self, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(tmp_path, monkeypatch)
        controller.switch_clip(clip)
        assert music.play_starts == [0.0]

        clock["now"] += 4.0
        controller.handle_udp_line("VISIBLE 0")

        assert music.events[-1] == "pause"
        assert controller.clip_positions[clip] == pytest.approx(4.0)

    def test_revealing_resumes_the_paused_channel_without_restarting(self, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(tmp_path, monkeypatch)
        controller.switch_clip(clip)
        clock["now"] += 4.0
        controller.handle_udp_line("VISIBLE 0")

        clock["now"] += 60.0  # hidden time must not advance the clip
        controller.handle_udp_line("VISIBLE 1")

        assert music.events[-1] == "unpause"
        assert music.play_starts == [0.0]  # resumed, never re-played
        clock["now"] += 1.0
        assert controller.current_position_for_active_clip() == pytest.approx(5.0)

    def test_a_clip_resumes_where_it_left_off_after_a_switch_away_and_back(self, tmp_path, monkeypatch):
        controller, music, clock, first = self._running(tmp_path, monkeypatch)
        second = tmp_path / "other.mp3"
        second.write_bytes(b"other")
        controller.switch_clip(first)
        clock["now"] += 3.0
        controller.switch_clip(second)
        clock["now"] += 2.0

        controller.switch_clip(first)

        assert music.loaded == str(first)
        # Back where it left off — three seconds in, not the top.
        assert music.play_starts[-1] == pytest.approx(3.0)

    def test_manual_pause_holds_even_while_visible_and_a_resume_lifts_it(self, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(tmp_path, monkeypatch)
        controller.switch_clip(clip)

        controller.set_manual_paused(True)
        assert music.events[-1] == "pause"

        controller.set_manual_paused(False)
        assert music.events[-1] == "unpause"

    def test_repeating_the_same_manual_pause_touches_nothing(self, tmp_path, monkeypatch, caplog):
        controller, music, clock, clip = self._running(tmp_path, monkeypatch)
        controller.switch_clip(clip)
        controller.set_manual_paused(True)
        before = list(music.events)

        with caplog.at_level(logging.INFO, logger="test.audio"):
            controller.set_manual_paused(True)

        assert music.events == before
        assert not caplog.records

    def test_switch_clip_none_stops_and_clears_flags(self, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(tmp_path, monkeypatch)
        controller.switch_clip(clip)

        controller.switch_clip(None)

        assert music.events[-1] == "stop"
        assert controller.current_path is None
        assert controller.paused is False
        assert controller.playback_running is False
        assert controller.play_start_position == 0.0

    def test_nothing_plays_while_the_mode_file_says_genau_is_inactive(self, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(tmp_path, monkeypatch)
        controller.mode_active = False

        controller.switch_clip(clip)

        assert "play" not in music.events

    def test_a_clip_line_finds_loads_and_plays_the_named_audio(self, tmp_path, monkeypatch):
        controller, music, clock, _clip = self._running(tmp_path, monkeypatch)
        named = tmp_path / "Named.mp3"
        named.write_bytes(b"named")

        controller.handle_udp_line("CLIP Named")

        assert music.loaded == str(named)
        assert music.busy

    def test_a_clip_line_with_no_audio_falls_silent_and_says_so(self, tmp_path, monkeypatch, caplog):
        controller, music, clock, clip = self._running(tmp_path, monkeypatch)
        controller.switch_clip(clip)

        with caplog.at_level(logging.WARNING, logger="test.audio"):
            controller.handle_udp_line("CLIP Missing")

        assert music.events[-1] == "stop"
        assert controller.current_path is None
        assert any("Missing" in r.getMessage() for r in caplog.records)

    def test_set_volume_scales_the_mixer(self, tmp_path: Path):
        music = MagicMock()
        controller = self._make_controller(tmp_path, music)

        controller.set_volume(30)

        music.set_volume.assert_called_once_with(0.3)

    def test_set_volume_is_noop_when_the_level_is_unchanged(self, tmp_path: Path):
        music = MagicMock()
        controller = self._make_controller(tmp_path, music)

        controller.set_volume(100)  # already full

        music.set_volume.assert_not_called()

    def test_a_force_muted_controller_never_touches_the_mixer(self, tmp_path: Path):
        """FUN_TIME_MUTE_AUDIO silences hidden and integration runs; no level the
        bridge publishes may bring their sound back."""
        music = MagicMock()
        controller = self._make_controller(tmp_path, music, force_muted=True)

        controller.set_volume(80)

        music.set_volume.assert_not_called()

    def test_normalize_position_wraps_when_clip_length_known(self, tmp_path: Path):
        controller = self._make_controller(tmp_path)
        clip = tmp_path / "demo.mp3"
        clip.write_bytes(b"demo")

        controller.clip_lengths[clip] = 10.0  # the cache the real lookup fills
        assert controller.normalize_position(clip, 12.5) == pytest.approx(2.5)
