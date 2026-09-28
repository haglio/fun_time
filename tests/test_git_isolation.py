from __future__ import annotations

import os

from tests.git_isolation import REPOSITORY_VARIABLES


def test_no_test_inherits_the_repository_a_git_command_was_running_in():
    assert not REPOSITORY_VARIABLES & os.environ.keys()
