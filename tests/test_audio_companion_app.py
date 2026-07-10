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


class TestAudioPlaybackController:
    def _make_controller(self, audio_companion_module, tmp_path: Path):
        return audio_companion_module.AudioPlaybackController(audio_folder=tmp_path, logger=logging.getLogger("test.audio"))

    def test_set_manual_paused_logs_and_updates_state(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)

        with patch.object(controller, "apply_state") as apply_state, \
             patch.object(controller.logger, "info") as info:
            controller.set_manual_paused(True)

        assert controller.manual_paused is True
        apply_state.assert_called_once_with()
        info.assert_called_once()

    def test_set_manual_paused_is_noop_when_state_is_unchanged(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)
        controller.manual_paused = True

        with patch.object(controller, "apply_state") as apply_state, \
             patch.object(controller.logger, "info") as info:
            controller.set_manual_paused(True)

        apply_state.assert_not_called()
        info.assert_not_called()

    def test_switch_clip_none_stops_and_clears_flags(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)
        controller.current_path = tmp_path / "demo.mp3"
        music = MagicMock()

        with patch.object(audio_companion_module.pygame.mixer, "music", music), \
             patch.object(controller, "save_active_clip_position") as save_position:
            controller.switch_clip(None)

        save_position.assert_called_once_with()
        music.stop.assert_called_once_with()
        assert controller.current_path is None
        assert controller.paused is False
        assert controller.playback_running is False
        assert controller.play_start_position == 0.0

    def test_apply_state_pauses_active_playback_when_hidden(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)
        controller.current_path = tmp_path / "demo.mp3"
        controller.visible = False
        controller.paused = False
        music = MagicMock()
        music.get_busy.return_value = True

        with patch.object(audio_companion_module.pygame.mixer, "music", music), \
             patch.object(controller, "save_active_clip_position") as save_position:
            controller.apply_state()

        save_position.assert_called_once_with()
        music.pause.assert_called_once_with()
        assert controller.paused is True
        assert controller.playback_running is False

    def test_apply_state_starts_playback_when_visible_and_idle(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)
        controller.current_path = tmp_path / "demo.mp3"
        controller.visible = True
        controller.mode_active = True
        controller.manual_paused = False
        music = MagicMock()
        music.get_busy.return_value = False

        with patch.object(audio_companion_module.pygame.mixer, "music", music), \
             patch.object(controller, "play_current_clip_from_saved_position") as play_current:
            controller.apply_state()

        play_current.assert_called_once_with()

    def test_apply_state_does_not_play_when_mode_file_says_genau_is_inactive(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)
        controller.current_path = tmp_path / "demo.mp3"
        controller.visible = True
        controller.mode_active = False
        music = MagicMock()
        music.get_busy.return_value = False

        with patch.object(audio_companion_module.pygame.mixer, "music", music), \
             patch.object(controller, "play_current_clip_from_saved_position") as play_current:
            controller.apply_state()

        play_current.assert_not_called()

    def test_read_mode_active_treats_only_1_as_enabled(self, audio_companion_module, tmp_path: Path):
        path = tmp_path / "genau_mode.txt"
        path.write_text("1", encoding="utf-8")
        assert audio_companion_module.read_mode_active(path) is True
        path.write_text("0", encoding="utf-8")
        assert audio_companion_module.read_mode_active(path) is False

    def test_handle_udp_line_switches_to_existing_clip(self, audio_companion_module, tmp_path: Path):
        clip = tmp_path / "Demo.mp3"
        clip.write_bytes(b"demo")
        controller = self._make_controller(audio_companion_module, tmp_path)

        with patch.object(controller, "switch_clip") as switch_clip:
            controller.handle_udp_line("CLIP Demo")

        switch_clip.assert_called_once_with(clip)

    def test_handle_udp_line_clears_clip_when_missing(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)

        with patch.object(controller, "switch_clip") as switch_clip, \
             patch.object(controller.logger, "warning") as warning:
            controller.handle_udp_line("CLIP Missing")

        switch_clip.assert_called_once_with(None)
        warning.assert_called_once()

    def test_handle_udp_line_updates_visibility_and_applies_state(self, audio_companion_module, tmp_path: Path):
        controller = self._make_controller(audio_companion_module, tmp_path)

        with patch.object(controller, "apply_state") as apply_state:
            controller.handle_udp_line("VISIBLE 1")
            controller.handle_udp_line("VISIBLE 0")

        assert controller.visible is False
        assert apply_state.call_count == 2

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

        with patch.object(controller, "get_clip_length", return_value=10.0):
            assert controller.normalize_position(clip, 12.5) == pytest.approx(2.5)
