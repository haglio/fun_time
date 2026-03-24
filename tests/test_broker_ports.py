from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import json

from fun_time.broker_ports import (
    ensure_mfp_serial_port,
    ensure_mfp_vlc_endpoint,
    read_mfp_vlc_endpoint,
    resolve_mfp_serial_port,
    resolve_virtual_port,
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
