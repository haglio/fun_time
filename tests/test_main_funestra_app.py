from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

from main_funestra import app
from main_funestra.window import replace_pygames_logo_with


def _the_app_source_a_test_cannot_run() -> ast.Module:
    return ast.parse((Path(__file__).resolve().parents[1] / "main_funestra" / "app.py")
                     .read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(_the_app_source_a_test_cannot_run())
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
    def test_the_main_funestra_opens_holding_its_item_with_the_rooms_sound_and_tiling(self):
        given = _keywords(_call(_function("_run"), "FunestraSetup"))

        assert given["locked"] == "True"
        assert given["sound_is_the_rooms"] == "True"
        assert given["tiles"] == "True"
        assert given["audible"] == "not audio_muted(args)"

    def test_the_files_fun_time_named_are_read_whole_off_the_command_line(self):
        given = _keywords(_call(_function("_run"), "FunestraSetup"))

        assert given["channels"] == "MainChannels.from_args(args)"

    def test_kino_runs_on_it_with_the_library_and_the_memory(self):
        given = _keywords(_call(_function("_run"), "FunestraSetup"))

        assert given["users"] == "_users(args, kino=kino_on(args, source=source, memory=memory))"

    def test_the_loop_is_the_shared_one_and_deals_the_windows_input(self):
        call = _call(_function("_run"), "run_the_window")

        assert [_said(a) for a in call.args] == ["pygame", "funestra", "Input(funestra).deal"]

    def test_the_library_is_read_under_the_loading_screen_of_the_window_already_open(self):
        run = _function("_run")

        assert _call(run, "_open_window").lineno < _call(run, "library_source").lineno
        assert _keywords(_call(run, "library_source"))["on_progress"] == "loading.update"


class TestGenauRunsOnItToo:
    def test_it_is_built_from_the_flags_fun_time_passes(self):
        given = _keywords(_call(_function("_genau"), "Genau"))
        flicks = _keywords(_call(_function("_genau"), "GenauFlicks"))
        outputs = _keywords(_call(_function("_genau"), "GenauOutputs"))
        channels = _keywords(_call(_function("_genau"), "GenauChannels"))

        assert flicks["folder"] == "args.flicks_dir"
        assert given["settings"] == "GenauSettings.read(args.config)"
        assert outputs["notifier"] == "GenauNotifier(args.notify_host, args.notify_port)"
        assert outputs["tcode_sink"] == "Osr2Sender(ROBOT_HAND, port=args.osr2_port)"
        assert outputs["messages"] == "MessageWriter(args.notice_file)"
        assert flicks["f_mode"] == "args.genau_f_mode"
        assert flicks["orientations"] == "orientations_from(args.genau_orientations)"
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

    def test_the_process_plays_ahead_of_background_work_from_before_the_window_opens(self):
        (ahead,) = [node for node in ast.walk(_function("main")) if isinstance(node, ast.With)
                    and _said(node.items[0].context_expr) == "ahead_on_the_processor()"]

        assert _call(ahead, "_run")


class TestAWindowFunTimeResizes:
    def test_the_window_takes_the_size_fun_time_gives_it_from_outside(self):
        assert _said(_call(_function("_open_window"), "take_outside_resizes").args[0]) == "pygame"


class TestTheWindowsIcon:
    def test_the_icon_is_worn_before_sdl_makes_the_window(self):
        opening = _function("_open_window")

        assert _call(opening, "replace_pygames_logo_with").lineno < _call(opening, "pygame.display.set_mode").lineno


class TestWhenSomethingCosmeticFails:
    def test_an_icon_it_cannot_read_is_said_rather_than_swallowed(self, tmp_path, caplog):
        not_an_icon = tmp_path / "icon.ico"
        not_an_icon.write_text("this is not an icon", encoding="utf-8")
        worn = []

        with caplog.at_level(logging.DEBUG, logger="main_funestra.window"):
            replace_pygames_logo_with(
                SimpleNamespace(display=SimpleNamespace(set_icon=worn.append)), not_an_icon)

        assert worn == []
        assert "icon" in caplog.records[0].getMessage().lower()


class TestWhichConfigTheFlagsAreReadAgainst:
    def _main(self, monkeypatch, argv):
        landed = []
        monkeypatch.setattr(app, "_run", lambda args: landed.append(args) or 0)
        app.main(argv)
        return landed[0]

    def test_a_named_config_supplies_the_defaults(self, tmp_path, monkeypatch):
        config = tmp_path / "fun_time_config.json"
        config.write_text('{"main_player": {"videos_dir": "C:/example/videos"}}', encoding="utf-8")

        args = self._main(monkeypatch, ["--config", str(config)])

        assert args.videos_dir == Path("C:/example/videos")

    def test_a_flag_still_beats_the_config_it_named(self, tmp_path, monkeypatch):
        config = tmp_path / "fun_time_config.json"
        config.write_text('{"main_player": {"videos_dir": "C:/example/videos"}}', encoding="utf-8")

        args = self._main(
            monkeypatch, ["--config", str(config), "--videos-dir", "C:/example/elsewhere"])

        assert args.videos_dir == Path("C:/example/elsewhere")
