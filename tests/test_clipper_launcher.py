from __future__ import annotations

from fun_time.robot_hand.clipper.launcher import (
    LAUNCHER_PREFILL_FALLBACK_NOTE,
    default_launcher_mode,
    prefill_note_text,
)


class TestDefaultLauncherMode:
    def test_prefers_new_when_vlc_prefill_exists(self):
        assert default_launcher_mode(has_vlc_prefill=True, has_last_session=True) == "new"

    def test_prefers_load_when_last_session_exists_and_no_prefill(self):
        assert default_launcher_mode(has_vlc_prefill=False, has_last_session=True) == "load"

    def test_prefers_new_when_no_last_session_exists(self):
        assert default_launcher_mode(has_vlc_prefill=False, has_last_session=False) == "new"


class TestPrefillNoteText:
    def test_uses_prefill_note_when_present(self):
        assert prefill_note_text("From VLC") == "From VLC"

    def test_uses_fallback_note_when_missing(self):
        assert prefill_note_text(None) == LAUNCHER_PREFILL_FALLBACK_NOTE
