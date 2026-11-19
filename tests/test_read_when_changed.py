from __future__ import annotations

from fun_time.read_when_changed import ReadWhenChanged
from fun_time.shared_state import BridgeState, read_shared_state, write_shared_state


def test_a_file_asked_for_many_times_is_read_again_only_after_it_is_written(tmp_path):
    state_file = tmp_path / "shared_state.ini"
    write_shared_state(state_file, BridgeState(main_latest=False))
    reads = []
    kept = ReadWhenChanged(state_file, lambda path: reads.append(path) or read_shared_state(path))

    before = [kept.latest() for _ask in range(5)]
    write_shared_state(state_file, BridgeState(main_latest=True))
    after = [kept.latest() for _ask in range(5)]

    assert ({state.main_latest for state in before}, {state.main_latest for state in after},
            len(reads)) == ({False}, {True}, 2)


def test_a_file_not_there_is_asked_of_the_reader_once_until_it_appears(tmp_path):
    state_file = tmp_path / "shared_state.ini"
    reads = []
    kept = ReadWhenChanged(state_file, lambda path: reads.append(path) or read_shared_state(path))

    missing = [kept.latest() for _ask in range(3)]
    write_shared_state(state_file, BridgeState())

    assert (missing, kept.latest(), len(reads)) == ([None, None, None], BridgeState(), 2)


def test_a_read_that_failed_is_tried_again_on_the_next_ask(tmp_path):
    state_file = tmp_path / "shared_state.ini"
    write_shared_state(state_file, BridgeState(main_latest=True))
    answers = iter([OSError("held by its writer"), None])
    reads = []

    def read(path):
        reads.append(path)
        failure = next(answers)
        if failure is not None:
            raise failure
        return read_shared_state(path)

    kept = ReadWhenChanged(state_file, read)

    assert (kept.latest(), kept.latest().main_latest, len(reads)) == (None, True, 2)
