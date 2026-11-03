from __future__ import annotations

from fun_time.broker_control import write_broker_command


def test_a_verb_waits_its_turn_behind_the_ones_the_broker_has_not_taken_yet(tmp_path):
    cmd_file = tmp_path / "broker_command.txt"

    write_broker_command(cmd_file, "park")
    write_broker_command(str(cmd_file), "resume")

    assert cmd_file.read_text(encoding="utf-8").split() == ["park", "resume"]
