from __future__ import annotations

import importlib
import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def audio_companion_module():
    fake_pygame = types.SimpleNamespace(mixer=types.SimpleNamespace(music=types.SimpleNamespace(), Sound=None))
    with patch.dict(sys.modules, {"pygame": fake_pygame}):
        module = importlib.import_module("fun_time.audio_companion_app")
        module = importlib.reload(module)
    return module


class TestFindAudio:
    def test_returns_first_supported_extension(self, audio_companion_module, tmp_path: Path):
        mp3 = tmp_path / "demo.mp3"
        wav = tmp_path / "demo.wav"
        mp3.write_bytes(b"mp3")
        wav.write_bytes(b"wav")

        assert audio_companion_module.find_audio(tmp_path, "demo") == mp3

    def test_returns_none_when_stem_missing(self, audio_companion_module, tmp_path: Path):
        assert audio_companion_module.find_audio(tmp_path, "missing") is None


class TestReadPausedState:
    def test_treats_only_1_as_paused(self, audio_companion_module, tmp_path: Path):
        path = tmp_path / "paused.txt"
        path.write_text("1", encoding="utf-8")
        assert audio_companion_module.read_paused_state(path) is True
        path.write_text("0", encoding="utf-8")
        assert audio_companion_module.read_paused_state(path) is False


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
    def _make_controller(self, audio_companion_module, tmp_path: Path):
        return audio_companion_module.AudioPlaybackController(audio_folder=tmp_path, logger=logging.getLogger("test.audio"))

    def _running(self, audio_companion_module, tmp_path: Path, monkeypatch):
        """A controller in genau mode with a real clip file, a remembering
        mixer, and a hand-turned clock."""
        music = _FakeMusic()
        monkeypatch.setattr(audio_companion_module.pygame.mixer, "music", music)
        clock = {"now": 100.0}
        monkeypatch.setattr(
            audio_companion_module, "time",
            types.SimpleNamespace(monotonic=lambda: clock["now"]),
        )
        clip = tmp_path / "demo.mp3"
        clip.write_bytes(b"demo")
        controller = self._make_controller(audio_companion_module, tmp_path)
        controller.mode_active = True
        controller.visible = True
        return controller, music, clock, clip

    def test_hiding_pauses_and_remembers_where_the_clip_was(self, audio_companion_module, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(audio_companion_module, tmp_path, monkeypatch)
        controller.switch_clip(clip)
        assert music.play_starts == [0.0]

        clock["now"] += 4.0
        controller.handle_udp_line("VISIBLE 0")

        assert music.events[-1] == "pause"
        assert controller.clip_positions[clip] == pytest.approx(4.0)

    def test_revealing_resumes_the_paused_channel_without_restarting(self, audio_companion_module, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(audio_companion_module, tmp_path, monkeypatch)
        controller.switch_clip(clip)
        clock["now"] += 4.0
        controller.handle_udp_line("VISIBLE 0")

        clock["now"] += 60.0  # hidden time must not advance the clip
        controller.handle_udp_line("VISIBLE 1")

        assert music.events[-1] == "unpause"
        assert music.play_starts == [0.0]  # resumed, never re-played
        clock["now"] += 1.0
        assert controller.current_position_for_active_clip() == pytest.approx(5.0)

    def test_a_clip_resumes_where_it_left_off_after_a_switch_away_and_back(self, audio_companion_module, tmp_path, monkeypatch):
        controller, music, clock, first = self._running(audio_companion_module, tmp_path, monkeypatch)
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

    def test_manual_pause_holds_even_while_visible_and_a_resume_lifts_it(self, audio_companion_module, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(audio_companion_module, tmp_path, monkeypatch)
        controller.switch_clip(clip)

        controller.set_manual_paused(True)
        assert music.events[-1] == "pause"

        controller.set_manual_paused(False)
        assert music.events[-1] == "unpause"

    def test_repeating_the_same_manual_pause_touches_nothing(self, audio_companion_module, tmp_path, monkeypatch, caplog):
        controller, music, clock, clip = self._running(audio_companion_module, tmp_path, monkeypatch)
        controller.switch_clip(clip)
        controller.set_manual_paused(True)
        before = list(music.events)

        with caplog.at_level(logging.INFO, logger="test.audio"):
            controller.set_manual_paused(True)

        assert music.events == before
        assert not caplog.records

    def test_switch_clip_none_stops_and_clears_flags(self, audio_companion_module, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(audio_companion_module, tmp_path, monkeypatch)
        controller.switch_clip(clip)

        controller.switch_clip(None)

        assert music.events[-1] == "stop"
        assert controller.current_path is None
        assert controller.paused is False
        assert controller.playback_running is False
        assert controller.play_start_position == 0.0

    def test_nothing_plays_while_the_mode_file_says_genau_is_inactive(self, audio_companion_module, tmp_path, monkeypatch):
        controller, music, clock, clip = self._running(audio_companion_module, tmp_path, monkeypatch)
        controller.mode_active = False

        controller.switch_clip(clip)

        assert "play" not in music.events

    def test_read_mode_active_treats_only_1_as_enabled(self, audio_companion_module, tmp_path: Path):
        path = tmp_path / "genau_mode.txt"
        path.write_text("1", encoding="utf-8")
        assert audio_companion_module.read_mode_active(path) is True
        path.write_text("0", encoding="utf-8")
        assert audio_companion_module.read_mode_active(path) is False

    def test_a_clip_line_finds_loads_and_plays_the_named_audio(self, audio_companion_module, tmp_path, monkeypatch):
        controller, music, clock, _clip = self._running(audio_companion_module, tmp_path, monkeypatch)
        named = tmp_path / "Named.mp3"
        named.write_bytes(b"named")

        controller.handle_udp_line("CLIP Named")

        assert music.loaded == str(named)
        assert music.busy

    def test_a_clip_line_with_no_audio_falls_silent_and_says_so(self, audio_companion_module, tmp_path, monkeypatch, caplog):
        controller, music, clock, clip = self._running(audio_companion_module, tmp_path, monkeypatch)
        controller.switch_clip(clip)

        with caplog.at_level(logging.WARNING, logger="test.audio"):
            controller.handle_udp_line("CLIP Missing")

        assert music.events[-1] == "stop"
        assert controller.current_path is None
        assert any("Missing" in r.getMessage() for r in caplog.records)

    def test_set_volume_scales_the_mixer(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)
        music = MagicMock()

        with patch.object(audio_companion_module.pygame.mixer, "music", music):
            controller.set_volume(30)

        music.set_volume.assert_called_once_with(0.3)

    def test_set_volume_is_noop_when_the_level_is_unchanged(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)
        music = MagicMock()

        with patch.object(audio_companion_module.pygame.mixer, "music", music):
            controller.set_volume(100)  # already full

        music.set_volume.assert_not_called()

    def test_a_force_muted_controller_never_touches_the_mixer(self, audio_companion_module, tmp_path: Path):
        """FUN_TIME_MUTE_AUDIO silences hidden and integration runs; no level the
        bridge publishes may bring their sound back."""
        controller = audio_companion_module.AudioPlaybackController(
            audio_folder=tmp_path, logger=logging.getLogger("test.audio"), force_muted=True,
        )
        music = MagicMock()

        with patch.object(audio_companion_module.pygame.mixer, "music", music):
            controller.set_volume(80)

        music.set_volume.assert_not_called()

    def test_normalize_position_wraps_when_clip_length_known(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)
        clip = tmp_path / "demo.mp3"
        clip.write_bytes(b"demo")

        controller.clip_lengths[clip] = 10.0  # the cache the real lookup fills
        assert controller.normalize_position(clip, 12.5) == pytest.approx(2.5)
