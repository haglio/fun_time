from __future__ import annotations

import logging

from app_support.logging_utils import install_exception_logging


def log_to_the_console(logger: logging.Logger) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    install_exception_logging(logger)
    return logger
