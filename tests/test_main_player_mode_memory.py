"""The mode the main player was last in, kept across sessions."""
from __future__ import annotations

from pathlib import Path

from main_player.library import FULL, MIXED, SHORTS
from main_player.mode_memory import ModeMemory, RememberedMode


class TestLengthMode:
    def test_the_record_it_writes_is_the_one_it_reads_back(self, tmp_path):
        """The file's own shape, not just the round trip.

        Reading it back through this same class would stay green under a
        renamed key or a reordered record, and `length_mode` is the name
        Evolver's vocabulary settled on for the length filter -- so the shape
        is written out here rather than derived from the writer.
        """
        path = tmp_path / "main_player_mode.txt"

        ModeMemory(path).write(RememberedMode(
            length_mode="shorts", compilation="Volume Six", video="C:/x/y.mp4"))

        written = path.read_text(encoding="utf-8")
        assert written.splitlines() == [
            "length_mode=shorts", "compilation=Volume Six", "video=C:/x/y.mp4"]
        assert written.endswith("\n"), "the last record is terminated like the others"

    def test_a_written_mode_reads_back(self, tmp_path):
        """Fun Time resumes the playlist a session closed on, so the mode that
        chose those videos has to survive the session too."""
        path = tmp_path / "state" / "main_player_mode.txt"

        ModeMemory(path).write(RememberedMode(length_mode=SHORTS))

        assert ModeMemory(path).read().length_mode == SHORTS

    def test_nothing_remembered_reads_empty(self, tmp_path):
        assert ModeMemory(tmp_path / "never_written.txt").read() == RememberedMode()

    def test_a_later_write_replaces_the_earlier_one(self, tmp_path):
        memory = ModeMemory(tmp_path / "main_player_mode.txt")

        memory.write(RememberedMode(length_mode=SHORTS))
        memory.write(RememberedMode(length_mode=FULL))

        assert memory.read().length_mode == FULL

    def test_a_mode_that_is_no_longer_a_mode_is_forgotten(self, tmp_path):
        """The file outlives the code that wrote it, so a word this build does
        not recognize must fall back rather than be handed to the library."""
        path = tmp_path / "main_player_mode.txt"
        path.write_text("length_mode=wildly-obsolete\n", encoding="utf-8")

        assert ModeMemory(path).read().length_mode == ""

    def test_without_a_path_it_is_inert(self):
        """No state dir configured: nothing is remembered, and nothing raises."""
        memory = ModeMemory(None)

        memory.write(RememberedMode(length_mode=MIXED))

        assert memory.read() == RememberedMode()


class TestWritingItDownWheneverItMoves:
    """The player asks this every frame, whichever path moved the mode -- a
    key, a Fun Time command, leaving a compilation -- because there is no one
    moment when it moves.  Sixty times a second, so what is already written
    down is what decides whether anything is written at all.
    """

    def test_a_mode_that_has_not_moved_is_not_written_again(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        memory = ModeMemory(path)
        memory.write(RememberedMode(length_mode=SHORTS))
        path.unlink()          # so a write would show up as the file coming back

        memory.sync(RememberedMode(length_mode=SHORTS))

        assert not path.exists()

    def test_a_mode_that_moved_is_written_down(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        memory = ModeMemory(path)
        memory.write(RememberedMode(length_mode=SHORTS))

        memory.sync(RememberedMode(length_mode=FULL))

        assert memory.read().length_mode == FULL

    def test_a_mode_it_wrote_itself_is_not_written_again_either(self, tmp_path):
        """The write has to be recorded by the sync that made it, or the very
        next frame finds the record stale and writes again -- and so does every
        frame after it, sixty times a second for the rest of the session."""
        path = tmp_path / "main_player_mode.txt"
        memory = ModeMemory(path)
        memory.sync(RememberedMode(length_mode=FULL))
        path.unlink()

        memory.sync(RememberedMode(length_mode=FULL))

        assert not path.exists()

    def test_what_was_read_back_at_startup_counts_as_already_written(self, tmp_path):
        """The main player opens on the mode it closed in, so the first frame's record
        matches the file and must not rewrite it."""
        path = tmp_path / "main_player_mode.txt"
        ModeMemory(path).write(RememberedMode(length_mode=SHORTS))
        memory = ModeMemory(path)
        remembered = memory.read()
        path.unlink()

        memory.sync(remembered)

        assert not path.exists()


class TestCompilation:
    def test_the_compilation_and_its_clip_read_back_with_the_mode(self, tmp_path):
        """The main player's playlist swap for a compilation is in memory only — the file
        Fun Time resumes never sees it — so being inside one is remembered here
        or not at all, and so is which of its clips was on screen: the resumed
        playlist cannot be rotated onto a clip it does not contain."""
        path = tmp_path / "main_player_mode.txt"
        mode = RememberedMode(length_mode=SHORTS, compilation="Vol6", video="C:/v/clip.mp4")

        ModeMemory(path).write(mode)

        assert ModeMemory(path).read() == mode

    def test_a_title_with_separators_in_it_survives(self, tmp_path):
        """Compilation titles carry dashes, spaces and parentheses; only the
        first "=" divides the line."""
        path = tmp_path / "main_player_mode.txt"
        title = "various - Ultimate Example Studio Alpha Collection - Volume 6 (v1)"

        ModeMemory(path).write(RememberedMode(length_mode=MIXED, compilation=title))

        assert ModeMemory(path).read().compilation == title

    def test_leaving_a_compilation_is_remembered_too(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        memory = ModeMemory(path)
        memory.write(RememberedMode(length_mode=SHORTS, compilation="Vol6"))

        memory.write(RememberedMode(length_mode=SHORTS))

        assert memory.read().compilation == ""


class TestWhenTheFileCannotBeRead:
    def test_a_mode_file_it_cannot_open_reads_as_nothing_remembered(
            self, tmp_path, monkeypatch):
        """The same answer as a first-ever launch: opening on the default mode
        is right, and refusing to start is not."""
        path = tmp_path / "main_player_mode.txt"
        ModeMemory(path).write(RememberedMode(length_mode=SHORTS))

        def refuse(*_args, **_kwargs):
            raise OSError("held open by something else")
        monkeypatch.setattr(Path, "read_text", refuse)

        assert ModeMemory(path).read() == RememberedMode()
