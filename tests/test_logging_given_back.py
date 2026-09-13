from __future__ import annotations

import logging

from app_support.logging_utils import configure_logging

from tests.logging_state import logging_given_back


def test_a_logger_configured_inside_propagates_again_afterwards(tmp_path):
    logger = logging.getLogger("fun_time.tests.given_back.propagate")

    with logging_given_back():
        configure_logging(logger.name, tmp_path / "given_back.log")

    assert logger.propagate is True


def test_a_log_file_opened_inside_is_let_go(tmp_path):
    log_file = tmp_path / "given_back.log"

    with logging_given_back():
        configure_logging("fun_time.tests.given_back.file", log_file)

    assert logging.getLogger("fun_time.tests.given_back.file").handlers == []
    log_file.unlink()


def test_a_level_set_inside_is_put_back():
    logger = logging.getLogger("fun_time.tests.given_back.level")

    with logging_given_back():
        logger.setLevel(logging.CRITICAL)

    assert logger.level == logging.NOTSET


def test_every_test_runs_inside_it(request):
    assert "_logging_is_given_back" in request.fixturenames
