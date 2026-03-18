"""Tests for fun_time.logging_utils."""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.logging_utils import (
    configure_logging,
    install_exception_logging,
)


class TestConfigureLogging:
    def test_returns_logger(self, tmp_path: Path):
        log_file = tmp_path / "test.log"
        logger = configure_logging("test.cfg.basic", log_file)
        assert isinstance(logger, logging.Logger)

    def test_creates_log_directory(self, tmp_path: Path):
        log_file = tmp_path / "subdir" / "app.log"
        configure_logging("test.cfg.mkdir", log_file)
        assert log_file.parent.exists()

    def test_file_handler_attached(self, tmp_path: Path):
        log_file = tmp_path / "app.log"
        logger = configure_logging("test.cfg.filehandler", log_file)
        handler_types = [type(h) for h in logger.handlers]
        assert logging.handlers.RotatingFileHandler in handler_types

    def test_no_console_handler_by_default(self, tmp_path: Path):
        log_file = tmp_path / "app.log"
        logger = configure_logging("test.cfg.noconsole", log_file)
        for h in logger.handlers:
            assert not isinstance(h, logging.StreamHandler) or isinstance(
                h, logging.handlers.RotatingFileHandler
            )

    def test_console_handler_added_when_requested(self, tmp_path: Path):
        log_file = tmp_path / "app.log"
        logger = configure_logging("test.cfg.console", log_file, console=True)
        stream_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(stream_handlers) == 1

    def test_existing_handlers_replaced_on_reconfigure(self, tmp_path: Path):
        log_file = tmp_path / "app.log"
        name = "test.cfg.reconfigure"
        configure_logging(name, log_file)
        configure_logging(name, log_file)
        logger = logging.getLogger(name)
        assert len(logger.handlers) == 1

    def test_logger_level_is_info(self, tmp_path: Path):
        log_file = tmp_path / "app.log"
        logger = configure_logging("test.cfg.level", log_file)
        assert logger.level == logging.INFO

    def test_propagation_disabled(self, tmp_path: Path):
        log_file = tmp_path / "app.log"
        logger = configure_logging("test.cfg.propagate", log_file)
        assert not logger.propagate

    def test_writes_to_log_file(self, tmp_path: Path):
        log_file = tmp_path / "app.log"
        logger = configure_logging("test.cfg.write", log_file)
        logger.info("hello test")
        for h in logger.handlers:
            h.flush()
        assert log_file.exists()
        assert "hello test" in log_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# logging.handlers import needed for isinstance checks above
# ---------------------------------------------------------------------------
import logging.handlers  # noqa: E402 – already imported indirectly, make explicit


class TestInstallExceptionLogging:
    def test_replaces_sys_excepthook(self, tmp_path: Path):
        original = sys.excepthook
        log_file = tmp_path / "exc.log"
        logger = configure_logging("test.exc.syshook", log_file)
        try:
            install_exception_logging(logger)
            assert sys.excepthook is not original
        finally:
            sys.excepthook = original

    def test_replaces_threading_excepthook(self, tmp_path: Path):
        original = threading.excepthook
        log_file = tmp_path / "exc.log"
        logger = configure_logging("test.exc.threadhook", log_file)
        try:
            install_exception_logging(logger)
            assert threading.excepthook is not original
        finally:
            threading.excepthook = original

    def test_sys_excepthook_ignores_keyboard_interrupt(self, tmp_path: Path):
        log_file = tmp_path / "exc.log"
        logger = configure_logging("test.exc.ki", log_file)
        install_exception_logging(logger)
        # Should not raise or log
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        for h in logger.handlers:
            h.flush()
        content = log_file.read_text(encoding="utf-8")
        assert "KeyboardInterrupt" not in content

    def test_sys_excepthook_logs_other_exceptions(self, tmp_path: Path):
        log_file = tmp_path / "exc.log"
        logger = configure_logging("test.exc.other", log_file)
        install_exception_logging(logger)
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            exc_type, exc, tb = sys.exc_info()
        sys.excepthook(exc_type, exc, tb)
        for h in logger.handlers:
            h.flush()
        assert "boom" in log_file.read_text(encoding="utf-8")
