from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time.controller_startup import prepare_random_favs_browser_manifest, restart_broker


def test_restart_broker_stops_existing_processes_and_launches_tray(tmp_path: Path):
    project_dir = tmp_path
    launch_path = project_dir / "launch_broker_tray.vbs"
    launch_path.write_text("", encoding="utf-8")

    with patch("fun_time.controller_startup.subprocess.run") as run, patch(
        "fun_time.controller_startup.subprocess.Popen"
    ) as popen, patch("fun_time.controller_startup.subprocess_window_kwargs", return_value={"creationflags": 1}):
        restart_broker(project_dir)

    run.assert_called_once()
    run_command = run.call_args.args[0]
    assert run_command[:4] == ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden"]
    assert "fun_time\\.broker_app" in run_command[-1]
    assert "launch_broker_tray\\.vbs" in run_command[-1]
    popen.assert_called_once_with(["wscript.exe", str(launch_path)], cwd=project_dir, creationflags=1)


def test_prepare_random_favs_browser_manifest_delegates_to_random_browser_builder(tmp_path: Path):
    output_path = tmp_path / "browser_manifest.txt"

    with patch("fun_time.controller_startup.build_manifest", return_value=("Profile 2", ["https://example.com"])) as build, patch(
        "fun_time.controller_startup.write_manifest"
    ) as write:
        prepare_random_favs_browser_manifest("config.json", output_path)

    build.assert_called_once_with("config.json")
    write.assert_called_once_with(output_path, "Profile 2", ["https://example.com"])
