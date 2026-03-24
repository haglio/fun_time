from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_broker_tray_uses_single_dynamic_start_restart_action():
    text = _read("scripts/broker_tray.ps1")

    assert "$actionItem = $menu.Items.Add('Start broker')" in text
    assert "Restart broker" in text
    assert "$startItem" not in text
    assert "$restartItem" not in text


def test_broker_tray_shows_status_as_disabled_menu_text():
    text = _read("scripts/broker_tray.ps1")

    assert "$statusItem = $menu.Items.Add('Broker status: unknown')" in text
    assert "$statusItem.Enabled = $false" in text
    assert "Status refresh" not in text
    assert "Close tray icon" in text
