from __future__ import annotations

import contextlib
import logging

_A_FRESH_LOGGER = (True, logging.NOTSET, [])


def _named_loggers() -> list[logging.Logger]:
    return [logger for logger in logging.Logger.manager.loggerDict.values()
            if isinstance(logger, logging.Logger)]


@contextlib.contextmanager
def logging_given_back():
    before = {logger: (logger.propagate, logger.level, list(logger.handlers))
              for logger in _named_loggers()}
    try:
        yield
    finally:
        for logger in _named_loggers():
            propagate, level, handlers = before.get(logger, _A_FRESH_LOGGER)
            for handler in logger.handlers:
                if handler not in handlers:
                    handler.close()
            logger.handlers[:] = handlers
            logger.propagate = propagate
            if logger.level != level:
                logger.setLevel(level)
