"""The Main Player's own module: the window it opens, and what it hands the Funestra.

``main_player.app`` is imported inside each test rather than at module scope: importing
it pulls pygame in for real, and the view tests that replace pygame with a mock
go red inside pygame's own resource lookup if that happens before they run.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

from main_player import app
from main_player.player_window import wear_the_icon


def _source() -> ast.Module:
    return ast.parse((Path(__file__).resolve().parents[1] / "main_player" / "app.py")
                     .read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(_source())
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _call(where: ast.AST, spelling: str) -> ast.Call:
    calls = [n for n in ast.walk(where)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == spelling]
    assert len(calls) == 1, f"expected one {spelling}(), found {len(calls)}"
    return calls[0]


def _said(node: ast.AST) -> str:
    return ast.unparse(node)


def _keywords(call: ast.Call) -> dict[str, str]:
    return {keyword.arg: _said(keyword.value) for keyword in call.keywords}


class TestWhatTheFunestraIsHanded:
    """`_run` needs a window and libmpv, so the wiring is read off the source: each
    of these is a mis-wiring every unit test would stay green through."""

    def test_the_main_funestra_opens_holding_its_item_with_the_rooms_sound_and_tiling(self):
        given = _keywords(_call(_function("_run"), "Funestra.on_window"))

        assert given["locked"] == "True"
        assert given["sound_is_the_rooms"] == "True"
        assert given["tiles"] == "True"
        assert given["audible"] == "not audio_muted(args)"

    def test_the_files_fun_time_named_are_read_whole_off_the_command_line(self):
        given = _keywords(_call(_function("_run"), "Funestra.on_window"))

        assert given["channels"] == "MainChannels.from_args(args)"

    def test_kino_runs_on_it_with_the_library_the_memory_and_the_notices(self):
        given = _keywords(_call(_function("_run"), "Funestra.on_window"))
        kino = _keywords(next(n for n in ast.walk(_function("_run"))
                              if isinstance(n, ast.Call) and _said(n.func) == "partial"
                              and _said(n.args[0]) == "Kino"))

        assert given["users"].startswith("_users(args, kino=partial(Kino,")
        assert kino["source"] == "source"
        assert kino["memory"] == "memory"
        assert kino["remembered"] == "remembered"
        assert kino["notices"] == "NoticeWriter(args.notice_file)"
        assert kino["resolve_playlist"] == "partial(resolve_playlist, args, source=source)"

    def test_the_window_is_measured_once_a_frame_and_both_parts_are_given_it(self):
        loop = next(n for n in ast.walk(_function("_run")) if isinstance(n, ast.While))

        assert _call(loop, "pygame.display.get_window_size")
        assert [_said(a) for a in _call(loop, "window_input.deal").args] == ["pygame.event.get()", "window"]
        assert _keywords(_call(loop, "funestra.tick")) == {"window": "window"}

    def test_the_events_are_dealt_before_the_frame_is_ticked(self):
        loop = next(n for n in ast.walk(_function("_run")) if isinstance(n, ast.While))

        assert _call(loop, "window_input.deal").lineno < _call(loop, "funestra.tick").lineno


class TestGenauRunsOnItToo:
    """Beside Kino, on the files Fun Time named: the window's own clips folder and
    drive file, the audio companion's address, and the OSR2's inlet."""

    def test_it_is_built_from_the_flags_fun_time_passes(self):
        given = _keywords(_call(_function("_genau"), "Genau"))
        channels = _keywords(_call(_function("_genau"), "GenauChannels"))

        assert given["clips_folder"] == "args.clips_dir"
        assert given["settings"] == "GenauSettings.read(args.genau_config)"
        assert given["notifier"] == "GenauNotifier(args.notify_host, args.notify_port)"
        assert given["tcode_sink"] == "UdpTCodeSink(host=args.tcode_host, port=args.tcode_port)"
        assert channels == {"command": "args.genau_command_file", "paused": "args.genau_paused_file",
                            "status": "args.genau_status_file", "drive": "args.drive_file"}

    def test_a_launch_naming_no_genau_files_runs_kino_alone(self):
        users = app._users(SimpleNamespace(genau_command_file=None), kino="kino")

        assert users == {"kino": "kino"}

    def test_a_launch_naming_them_puts_genau_beside_kino(self):
        args = SimpleNamespace(
            genau_command_file=Path("C:/example/state/genau_cmd.txt"),
            genau_paused_file=Path("C:/example/state/genau_paused.txt"),
            genau_status_file=Path("C:/example/state/genau_status.txt"),
            drive_file=Path("C:/example/state/genau_drive.txt"),
        )

        users = app._users(args, kino="kino")

        assert list(users) == ["kino", "genau"]
        assert callable(users["genau"])

    def test_the_process_runs_ahead_of_background_work_before_the_window_opens(self):
        main = _function("main")

        assert (_call(main, "run_ahead_of_background_work").lineno
                < _call(main, "_run").lineno)


class TestAWindowFunTimeResizes:
    def test_the_window_takes_the_size_fun_time_gives_it_from_outside(self):
        assert _said(_call(_function("_open_window"), "take_outside_resizes").args[0]) == "pygame"


class TestWhenSomethingCosmeticFails:
    def test_an_icon_it_cannot_read_is_said_rather_than_swallowed(self, tmp_path, caplog):
        not_an_icon = tmp_path / "icon.ico"
        not_an_icon.write_text("this is not an icon", encoding="utf-8")
        worn = []

        with caplog.at_level(logging.DEBUG, logger="main_player.player_window"):
            wear_the_icon(SimpleNamespace(display=SimpleNamespace(set_icon=worn.append)),
                          not_an_icon)

        assert worn == []
        assert "icon" in caplog.records[0].getMessage().lower()


class TestWhichConfigTheFlagsAreReadAgainst:
    """The Main Player parses twice on purpose.  The first pass exists only to find out
    whether ``--config`` names a file; if it does, the parser is built again
    from THAT file, because every default it feeds comes from it."""

    def _main(self, monkeypatch, argv):
        landed = []
        monkeypatch.setattr(app, "_run", lambda args: landed.append(args) or 0)
        app.main(argv)
        return landed[0]

    def test_a_named_config_supplies_the_defaults(self, tmp_path, monkeypatch):
        config = tmp_path / "genau_config.json"
        config.write_text('{"main_player": {"tcode_udp_port": 51000}}', encoding="utf-8")

        args = self._main(monkeypatch, ["--config", str(config)])

        assert args.tcode_port == 51000

    def test_a_flag_still_beats_the_config_it_named(self, tmp_path, monkeypatch):
        config = tmp_path / "genau_config.json"
        config.write_text('{"main_player": {"tcode_udp_port": 51000}}', encoding="utf-8")

        args = self._main(
            monkeypatch, ["--config", str(config), "--tcode-port", "50999"])

        assert args.tcode_port == 50999
