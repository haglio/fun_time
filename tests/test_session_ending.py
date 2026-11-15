from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.manifest import LaunchManifest, write_windows_bridge_manifest
from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    SHUTDOWN_PHASES,
    SHUTDOWN_PROGRESS_FILENAME,
    NullProgress,
    parse_progress,
    ready_file_for,
)
from fun_time.session_ending import (
    CLOSING_SCREEN_READY_CEILING_S,
    SessionEnding,
    TheControls,
    closing_screen,
    end_the_session,
    esc_called_the_quit_off,
    wait_for_the_closing_screen,
)
from fun_time.session_handoff import VR, request_handoff


@dataclass
class _Heard:
    said: list[str] = field(default_factory=list)


class _Dispatching:
    def __init__(self, heard: _Heard) -> None:
        self._heard = heard

    def stop(self) -> None:
        self._heard.said.append("dispatching stops")

    def join(self, timeout: float) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def close(self) -> None:
        self._heard.said.append("dispatch files close")


@dataclass
class _ItsOwn:
    heard: _Heard
    asked_with: list[tuple] = field(default_factory=list)

    @contextlib.contextmanager
    def closing_screen(self, crossing, hint):
        self.asked_with.append((crossing, hint))
        self.heard.said.append("closing screen up")
        yield NullProgress()
        self.heard.said.append("closing screen down")

    def end_the_children(self, shutdown, crossing, hint) -> None:
        self.heard.said.append("children end")

    def stop_the_keys(self) -> None:
        self.heard.said.append("keys stop")

    def last_of_all(self, crossing, hint) -> None:
        self.heard.said.append("last of all")


@pytest.fixture
def manifest(cfg_factory, tmp_path) -> LaunchManifest:
    return LaunchManifest.read(write_windows_bridge_manifest(
        load_config(cfg_factory()), tmp_path / "launch.ini"))


def _an_ending(manifest: LaunchManifest, state_dir: Path, heard: _Heard, **given) -> SessionEnding:
    dispatching = _Dispatching(heard)
    return SessionEnding(
        state_dir=state_dir, manifest=manifest,
        controls=TheControls(dispatch_runner=dispatching, dispatch_thread=dispatching),
        **{"quit_hint": "Press Esc to cancel closing", "esc_over_a_crossing": False, **given})


class TestTheOneEnding:
    def test_it_stops_the_session_then_ends_its_parts_under_the_closing_screen(
            self, manifest, tmp_path):
        heard = _Heard()

        end_the_session(_an_ending(manifest, tmp_path, heard), _ItsOwn(heard), asked=True)

        assert heard.said == [
            "dispatching stops", "closing screen up", "dispatch files close",
            "children end", "keys stop", "last of all", "closing screen down"]

    def test_a_crossing_leaves_the_keys_to_hear_esc_for_the_relay(self, manifest, tmp_path):
        heard = _Heard()
        request_handoff(tmp_path, VR)

        end_the_session(_an_ending(manifest, tmp_path, heard), _ItsOwn(heard), asked=True)

        assert "keys stop" not in heard.said

    def test_only_a_quit_he_asked_for_offers_esc(self, manifest, tmp_path):
        own = _ItsOwn(_Heard())

        end_the_session(_an_ending(manifest, tmp_path, own.heard), own, asked=False)
        end_the_session(_an_ending(manifest, tmp_path, own.heard), own, asked=True)

        assert [hint for _crossing, hint in own.asked_with] == ["", "Press Esc to cancel closing"]

    @pytest.mark.parametrize(("over_a_crossing", "offered"), [
        (False, ""), (True, VR.crossing_hint)], ids=["desktop", "headset"])
    def test_over_a_crossing_esc_is_offered_only_where_the_ending_says(
            self, manifest, tmp_path, over_a_crossing, offered):
        own = _ItsOwn(_Heard())
        request_handoff(tmp_path, VR)

        end_the_session(_an_ending(manifest, tmp_path, own.heard,
                                   esc_over_a_crossing=over_a_crossing), own, asked=True)

        assert own.asked_with == [(VR, offered)]

    def test_the_funestras_are_silent_and_the_osr2_held_before_the_closing_screen_goes_up(
            self, manifest, tmp_path):
        heard = _Heard()
        commands = manifest.commands

        class Watching(_ItsOwn):
            @contextlib.contextmanager
            def closing_screen(self, crossing, hint):
                heard.said.append(Path(commands.main_funestra_paused_file).read_text(
                    encoding="utf-8"))
                heard.said.append(Path(commands.genau_cmd_file).read_text(
                    encoding="utf-8").split()[-1])
                yield NullProgress()

        end_the_session(_an_ending(manifest, tmp_path, heard), Watching(heard), asked=True)

        assert heard.said[1:3] == ["1", "PAUSE"]


@pytest.mark.real_startup_waits
class TestWaitingForTheClosingScreen:
    def test_the_flag_landing_is_what_the_ending_was_waiting_for(self, tmp_path):
        ready = tmp_path / "shutdown_ready.flag"
        ready.write_text("", encoding="utf-8")

        assert wait_for_the_closing_screen(ready, lambda: True, sleep=lambda _s: None)

    def test_a_painter_already_gone_is_not_waited_for(self, tmp_path, caplog):
        with caplog.at_level("WARNING", logger="fun_time.session_ending"):
            painted = wait_for_the_closing_screen(
                tmp_path / "shutdown_ready.flag", lambda: False, sleep=lambda _s: None)

        assert not painted
        assert "exited before it was ready" in caplog.text

    def test_a_silent_painter_is_given_up_on_at_the_deadline(self, tmp_path, caplog):
        clock = [0.0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        with caplog.at_level("WARNING", logger="fun_time.session_ending"):
            painted = wait_for_the_closing_screen(
                tmp_path / "shutdown_ready.flag", lambda: True,
                now=lambda: clock[0], sleep=sleep)

        assert not painted
        assert "anyway" in caplog.text
        assert clock[0] == pytest.approx(CLOSING_SCREEN_READY_CEILING_S)


@dataclass
class _Painter:
    progress_file: Path
    heard: list[str] = field(default_factory=list)

    def raise_it(self) -> None:
        line = parse_progress(self.progress_file.read_text(encoding="utf-8"))
        self.heard.append(f"raised over {line.message}")
        ready_file_for(self.progress_file).write_text("", encoding="utf-8")

    def still_up(self) -> bool:
        return True

    def take_it_down(self, progress) -> None:
        self.heard.append("taken down")


class TestTheOneClosingScreen:
    def test_its_first_line_is_written_before_anything_is_asked_to_paint_it(self, tmp_path):
        progress_file = tmp_path / SHUTDOWN_PROGRESS_FILENAME
        painter = _Painter(progress_file)

        with closing_screen(progress_file, phases=SHUTDOWN_PHASES, hint="", painter=painter):
            pass

        assert painter.heard == [f"raised over {SHUTDOWN_PHASES[0].message}", "taken down"]

    def test_it_says_what_esc_would_cancel(self, tmp_path):
        progress_file = tmp_path / SHUTDOWN_PROGRESS_FILENAME

        with closing_screen(progress_file, phases=SHUTDOWN_PHASES, hint="Press Esc to stop",
                            painter=_Painter(progress_file)):
            line = parse_progress(progress_file.read_text(encoding="utf-8"))

        assert line.hint == "Press Esc to stop"

    def test_a_flag_from_a_previous_session_cannot_vouch_for_this_one(self, tmp_path):
        progress_file = tmp_path / SHUTDOWN_PROGRESS_FILENAME
        stale = ready_file_for(progress_file)
        stale.write_text("", encoding="utf-8")
        seen: list[bool] = []

        class Gone(_Painter):
            def raise_it(self) -> None:
                seen.append(stale.exists())

            def still_up(self) -> bool:
                return False

        with closing_screen(progress_file, phases=SHUTDOWN_PHASES, hint="",
                            painter=Gone(progress_file)):
            pass

        assert seen == [False]

    def test_it_leaves_no_file_behind_even_when_the_ending_fails(self, tmp_path):
        progress_file = tmp_path / SHUTDOWN_PROGRESS_FILENAME
        painter = _Painter(progress_file)

        with pytest.raises(RuntimeError), closing_screen(
                progress_file, phases=SHUTDOWN_PHASES, hint="", painter=painter):
            raise RuntimeError("a part that would not end")

        assert painter.heard[-1] == "taken down"
        assert not progress_file.exists()
        assert not ready_file_for(progress_file).exists()


class TestEscOnTheClosingScreen:
    def test_esc_over_a_quit_he_was_offered_it_for_calls_the_quit_off(self, tmp_path):
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        assert esc_called_the_quit_off(tmp_path, None, "Press Esc to cancel closing")

    def test_esc_never_offered_calls_nothing_off(self, tmp_path):
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        assert not esc_called_the_quit_off(tmp_path, None, "")

    def test_esc_over_a_crossing_is_the_crossings_to_answer(self, tmp_path):
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        assert not esc_called_the_quit_off(tmp_path, VR, VR.crossing_hint)

    def test_the_quit_chord_after_esc_lets_the_quit_go_on(self, tmp_path):
        (tmp_path / CANCEL_FILENAME).write_text("cancel\nquit\n", encoding="utf-8")

        assert not esc_called_the_quit_off(tmp_path, None, "Press Esc to cancel closing")


class TestTheControls:
    def test_voice_then_the_dispatch_files_then_the_loopback_server_stop(self):
        heard = _Heard()

        class Voice:
            def stop(self) -> None:
                heard.said.append("voice stops")

        class Server:
            def shutdown(self) -> None:
                heard.said.append("loopback stops serving")

            def server_close(self) -> None:
                heard.said.append("loopback closes")

        dispatching = _Dispatching(heard)
        TheControls(dispatching, dispatching, Voice(), None, Server()).stop()

        assert heard.said == [
            "voice stops", "dispatch files close", "loopback stops serving", "loopback closes"]
