from __future__ import annotations

from pathlib import Path

from fun_time.robot_hand.clipper.launcher import (
    LAUNCHER_PREFILL_FALLBACK_NOTE,
    default_launcher_mode_for_session,
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


class TestDefaultLauncherModeForSession:
    def test_prefers_load_when_last_session_matches_prefilled_session_name(self):
        assert (
            default_launcher_mode_for_session(
                vlc_session_name="demo clip",
                last_session_json="C:\\sessions\\demo clip.json",
            )
            == "load"
        )

    def test_prefers_new_when_last_session_name_differs_from_prefilled_session_name(self):
        assert (
            default_launcher_mode_for_session(
                vlc_session_name="demo clip",
                last_session_json="C:\\sessions\\other clip.json",
            )
            == "new"
        )

    def test_ignores_blank_last_session_path(self):
        assert default_launcher_mode_for_session(vlc_session_name="demo clip", last_session_json="") == "new"

    def test_matches_last_session_path_by_stem(self):
        assert default_launcher_mode_for_session(vlc_session_name="demo clip", last_session_json=str(Path("demo clip.json"))) == "load"


class TestPrefillNoteText:
    def test_uses_prefill_note_when_present(self):
        assert prefill_note_text("From VLC") == "From VLC"

    def test_uses_fallback_note_when_missing(self):
        assert prefill_note_text(None) == LAUNCHER_PREFILL_FALLBACK_NOTE
