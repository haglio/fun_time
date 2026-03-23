from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from fun_time.broker_ports import resolve_virtual_port
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
