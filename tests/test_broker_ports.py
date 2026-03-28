from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import json

from fun_time.broker_ports import (
    _read_mfp_config_payload,
    collect_com0com_ports,
    ensure_mfp_serial_port,
    ensure_mfp_vlc_endpoint,
    read_mfp_selected_serial_port,
    read_mfp_vlc_endpoint,
    resolve_mfp_serial_port,
    resolve_virtual_port,
    write_mfp_vlc_endpoint,
)
from fun_time.config import load_config


class TestResolveVirtualPort:
    def test_returns_configured_port_when_present(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")

        with patch(
            "fun_time.broker_ports.collect_com0com_ports",
            return_value={"COM15": ("com0com - serial port emulator", "COM0COM\\PORT\\CNCB2")},
        ):
            result = resolve_virtual_port(config, "COM15", logger)

        assert result == "COM15"

    def test_prefers_broker_side_matching_mfp_selection(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")

        with patch(
            "fun_time.broker_ports.collect_com0com_ports",
            return_value={
                "COM7": ("com0com - serial port emulator CNCA1", "COM0COM\\PORT\\CNCA1"),
                "COM8": ("com0com - serial port emulator CNCB1", "COM0COM\\PORT\\CNCB1"),
            },
        ), patch("fun_time.broker_ports.read_mfp_selected_serial_port", return_value="COM0COM\\PORT\\CNCA1"):
            result = resolve_virtual_port(config, "COM15", logger)

        assert result == "COM8"

    def test_falls_back_to_only_cncb_port(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")

        with patch(
            "fun_time.broker_ports.collect_com0com_ports",
            return_value={
                "COM7": ("com0com - serial port emulator CNCA1", "COM0COM\\PORT\\CNCA1"),
                "COM8": ("com0com - serial port emulator CNCB1", "COM0COM\\PORT\\CNCB1"),
            },
        ), patch("fun_time.broker_ports.read_mfp_selected_serial_port", return_value=None):
            result = resolve_virtual_port(config, "COM15", logger)

        assert result == "COM8"


class TestResolveMfpSerialPort:
    def test_keeps_selected_port_when_present(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")

        with patch(
            "fun_time.broker_ports.collect_com0com_ports",
            return_value={
                "COM7": ("com0com - serial port emulator CNCA1", "COM0COM\\PORT\\CNCA1"),
                "COM8": ("com0com - serial port emulator CNCB1", "COM0COM\\PORT\\CNCB1"),
            },
        ), patch("fun_time.broker_ports.read_mfp_selected_serial_port", return_value="COM0COM\\PORT\\CNCA1"):
            result = resolve_mfp_serial_port(config, logger)

        assert result == "COM0COM\\PORT\\CNCA1"

    def test_prefers_matching_cnca_side_for_resolved_broker_port(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")

        with patch(
            "fun_time.broker_ports.collect_com0com_ports",
            return_value={
                "COM7": ("com0com - serial port emulator CNCA1", "COM0COM\\PORT\\CNCA1"),
                "COM8": ("com0com - serial port emulator CNCB1", "COM0COM\\PORT\\CNCB1"),
            },
        ), patch("fun_time.broker_ports.read_mfp_selected_serial_port", return_value="COM0COM\\PORT\\CNCA2"):
            result = resolve_mfp_serial_port(config, logger)

        assert result == "COM0COM\\PORT\\CNCA1"

    def test_ensure_mfp_serial_port_updates_config_when_stale(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")
        mfp_config_path = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config_path.write_text(
            json.dumps(
                {
                    "OutputTarget": {
                        "Items": [
                            {
                                "SelectedSerialPort": "COM0COM\\PORT\\CNCA2",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        with patch(
            "fun_time.broker_ports.collect_com0com_ports",
            return_value={
                "COM7": ("com0com - serial port emulator CNCA1", "COM0COM\\PORT\\CNCA1"),
                "COM8": ("com0com - serial port emulator CNCB1", "COM0COM\\PORT\\CNCB1"),
            },
        ):
            result = ensure_mfp_serial_port(config, logger)

        assert result == "COM0COM\\PORT\\CNCA1"
        payload = json.loads(mfp_config_path.read_text(encoding="utf-8"))
        assert payload["OutputTarget"]["Items"][0]["SelectedSerialPort"] == "COM0COM\\PORT\\CNCA1"


class TestEnsureMfpVlcEndpoint:
    def test_reads_existing_vlc_endpoint(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config_path = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config_path.write_text(
            json.dumps(
                {
                    "MediaSource": {
                        "VLC": {
                            "Endpoint": "127.0.0.1:8080",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        assert read_mfp_vlc_endpoint(config) == "127.0.0.1:8080"

    def test_updates_mfp_vlc_endpoint_to_primary_port(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")
        mfp_config_path = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config_path.write_text(
            json.dumps(
                {
                    "MediaSource": {
                        "ActiveItem": "Web",
                        "VLC": {
                            "AutoConnectEnabled": False,
                            "Endpoint": "127.0.0.1:8080",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        result = ensure_mfp_vlc_endpoint(config, logger)

        assert result == "127.0.0.1:8090"
        payload = json.loads(mfp_config_path.read_text(encoding="utf-8"))
        assert payload["MediaSource"]["ActiveItem"] == "VLC"
        assert payload["MediaSource"]["VLC"]["AutoConnectEnabled"] is True
        assert payload["MediaSource"]["VLC"]["Endpoint"] == "127.0.0.1:8090"


# --- Edge-case / fallback coverage ---


class TestResolveVirtualPortFallbacks:
    def test_returns_configured_port_when_multiple_cncb_and_no_match(self, cfg_path: Path):
        """When configured port is missing and multiple CNCB ports exist, return configured as fallback."""
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")
        with patch(
            "fun_time.broker_ports.collect_com0com_ports",
            return_value={
                "COM7": ("com0com CNCA1", "COM0COM\\PORT\\CNCA1"),
                "COM8": ("com0com CNCB1", "COM0COM\\PORT\\CNCB1"),
                "COM9": ("com0com CNCB2", "COM0COM\\PORT\\CNCB2"),
            },
        ), patch("fun_time.broker_ports.read_mfp_selected_serial_port", return_value=None):
            result = resolve_virtual_port(config, "COM99", logger)
        assert result == "COM99"


class TestResolveMfpSerialPortFallbacks:
    def test_falls_back_to_sole_cnca_port(self, cfg_path: Path):
        """When broker port match doesn't find a CNCA pair, fall back to the sole CNCA port."""
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")
        with patch(
            "fun_time.broker_ports.collect_com0com_ports",
            return_value={
                "COM7": ("com0com CNCA1", "COM0COM\\PORT\\CNCA1"),
                "COM8": ("com0com CNCB1", "COM0COM\\PORT\\CNCB1"),
            },
        ), patch("fun_time.broker_ports.read_mfp_selected_serial_port", return_value="STALE_PORT"), \
             patch("fun_time.broker_ports.resolve_virtual_port", return_value="COM99"):
            result = resolve_mfp_serial_port(config, logger)
        assert result == "COM0COM\\PORT\\CNCA1"

    def test_returns_selected_port_when_no_com0com_ports(self, cfg_path: Path):
        """When no com0com ports exist at all, return whatever was selected."""
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")
        with patch("fun_time.broker_ports.collect_com0com_ports", return_value={}), \
             patch("fun_time.broker_ports.read_mfp_selected_serial_port", return_value="COM5"), \
             patch("fun_time.broker_ports.resolve_virtual_port", return_value="COM99"):
            result = resolve_mfp_serial_port(config, logger)
        assert result == "COM5"


class TestEnsureMfpSerialPortEdgeCases:
    def test_returns_none_when_resolved_is_none(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")
        with patch("fun_time.broker_ports.resolve_mfp_serial_port", return_value=None):
            assert ensure_mfp_serial_port(config, logger) is None

    def test_returns_current_when_already_matches(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")
        with patch("fun_time.broker_ports.resolve_mfp_serial_port", return_value="COM7"), \
             patch("fun_time.broker_ports.read_mfp_selected_serial_port", return_value="COM7"):
            result = ensure_mfp_serial_port(config, logger)
        assert result == "COM7"


class TestEnsureMfpVlcEndpointEdgeCases:
    def test_returns_current_when_already_matches(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.broker")
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({
            "MediaSource": {"VLC": {"Endpoint": f"127.0.0.1:{config.controller.primary_vlc_http_port}"}}
        }), encoding="utf-8")
        result = ensure_mfp_vlc_endpoint(config, logger)
        assert result == f"127.0.0.1:{config.controller.primary_vlc_http_port}"


class TestMfpConfigEdgeCases:
    def test_read_mfp_config_returns_empty_when_file_missing(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        if mfp_config.exists():
            mfp_config.unlink()
        assert _read_mfp_config_payload(config) == {}

    def test_read_mfp_config_returns_empty_for_invalid_json(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text("NOT JSON", encoding="utf-8")
        assert _read_mfp_config_payload(config) == {}

    def test_read_mfp_selected_serial_port_returns_none_when_missing(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        if mfp_config.exists():
            mfp_config.unlink()
        assert read_mfp_selected_serial_port(config) is None

    def test_read_mfp_vlc_endpoint_returns_none_for_missing_keys(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": "not_a_dict"}), encoding="utf-8")
        assert read_mfp_vlc_endpoint(config) is None

    def test_read_mfp_vlc_endpoint_returns_none_when_vlc_not_dict(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": {"VLC": "not_a_dict"}}), encoding="utf-8")
        assert read_mfp_vlc_endpoint(config) is None

    def test_read_mfp_vlc_endpoint_returns_none_when_endpoint_not_string(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": {"VLC": {"Endpoint": 12345}}}), encoding="utf-8")
        assert read_mfp_vlc_endpoint(config) is None

    def test_write_mfp_vlc_endpoint_repairs_malformed_media_source(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": "broken"}), encoding="utf-8")
        write_mfp_vlc_endpoint(config, "127.0.0.1:8080")
        payload = json.loads(mfp_config.read_text(encoding="utf-8"))
        assert payload["MediaSource"]["VLC"]["Endpoint"] == "127.0.0.1:8080"

    def test_write_mfp_vlc_endpoint_repairs_malformed_vlc_source(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": {"VLC": "broken"}}), encoding="utf-8")
        write_mfp_vlc_endpoint(config, "127.0.0.1:8080")
        payload = json.loads(mfp_config.read_text(encoding="utf-8"))
        assert payload["MediaSource"]["VLC"]["Endpoint"] == "127.0.0.1:8080"

    def test_collect_com0com_ports_skips_ports_without_device(self, monkeypatch):
        class FakePort:
            device = None
            description = "com0com"
            hwid = "COM0COM\\PORT\\CNCA1"
        monkeypatch.setattr("fun_time.broker_ports.iter_serial_ports", lambda: [FakePort()])
        assert collect_com0com_ports() == {}

    def test_collect_com0com_ports_skips_non_com0com_ports(self, monkeypatch):
        class FakePort:
            device = "COM3"
            description = "USB Serial Port"
            hwid = "USB\\VID_1234"
        monkeypatch.setattr("fun_time.broker_ports.iter_serial_ports", lambda: [FakePort()])
        assert collect_com0com_ports() == {}

    def test_collect_com0com_ports_collects_matching_ports(self, monkeypatch):
        class FakePort:
            device = "COM7"
            description = "com0com - serial port emulator CNCA1"
            hwid = "COM0COM\\PORT\\CNCA1"
        monkeypatch.setattr("fun_time.broker_ports.iter_serial_ports", lambda: [FakePort()])
        result = collect_com0com_ports()
        assert "COM7" in result
