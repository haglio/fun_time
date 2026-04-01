"""Tests for dashboard taskbar close handler."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time.dashboard_app import build_dashboard_window, load_dashboard_app_config
from fun_time.config import load_config
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.dashboard_layout import Size


@patch("fun_time.dashboard_app.get_preview_monitor_sizes", return_value=(Size(2560, 1392), Size(1440, 3440)))
def test_close_handler_writes_exit_to_ahk_cmd_file(mock_monitors, cfg_path: Path):
    """WM_DELETE_WINDOW handler should write 'exit' to ahk_cmd.txt in state dir."""
    config = load_config(cfg_path)
    manifest_path = write_windows_bridge_manifest(config, "vlc-pass")
    app_config = load_dashboard_app_config(manifest_path)

    root = build_dashboard_window(app_config)
    try:
        ahk_cmd_file = manifest_path.parent / "ahk_cmd.txt"
        assert not ahk_cmd_file.exists(), "ahk_cmd.txt should not exist before close"

        # A WM_DELETE_WINDOW handler must be registered.
        handler_cmd = root.protocol("WM_DELETE_WINDOW")
        assert handler_cmd, "WM_DELETE_WINDOW protocol handler should be registered"

        # Invoke the handler via the tcl interpreter.
        root.tk.eval(handler_cmd)
    except Exception:
        try:
            root.destroy()
        except Exception:
            pass
        raise
    else:
        try:
            root.destroy()
        except Exception:
            pass

    assert ahk_cmd_file.exists(), "Close handler should have written ahk_cmd.txt"
    assert ahk_cmd_file.read_text(encoding="utf-8") == "exit"
