from __future__ import annotations

from fun_time.controller_startup_app import build_parser, main


def test_build_parser_accepts_restart_broker_command():
    args = build_parser().parse_args(["restart-broker", "--project-dir", "C:\\FunTime"])

    assert args.command == "restart-broker"
    assert args.project_dir == "C:\\FunTime"


def test_build_parser_accepts_manifest_command():
    args = build_parser().parse_args(
        [
            "prepare-random-favs-browser-manifest",
            "--config",
            "fun_time_config.json",
            "--output",
            "state\\random_favs_browser_urls.txt",
        ]
    )

    assert args.command == "prepare-random-favs-browser-manifest"
    assert args.config == "fun_time_config.json"


def test_main_dispatches_restart_broker(monkeypatch):
    recorded: dict[str, str] = {}

    def fake_restart(project_dir: str) -> None:
        recorded["project_dir"] = project_dir

    monkeypatch.setattr("fun_time.controller_startup_app.restart_broker", fake_restart)

    code = main(["restart-broker", "--project-dir", "C:\\FunTime"])

    assert code == 0
    assert recorded["project_dir"] == "C:\\FunTime"


def test_main_dispatches_manifest_prep(monkeypatch):
    recorded: dict[str, str] = {}

    def fake_prepare(config_path: str, output_path: str) -> None:
        recorded["config"] = config_path
        recorded["output"] = output_path

    monkeypatch.setattr("fun_time.controller_startup_app.prepare_random_favs_browser_manifest", fake_prepare)

    code = main(
        [
            "prepare-random-favs-browser-manifest",
            "--config",
            "fun_time_config.json",
            "--output",
            "state\\random_favs_browser_urls.txt",
        ]
    )

    assert code == 0
    assert recorded["config"] == "fun_time_config.json"
    assert recorded["output"] == "state\\random_favs_browser_urls.txt"
