from __future__ import annotations

from pathlib import Path

from main_funestra.mode_memory import ModeMemory, RememberedMode

INSIDE = RememberedMode(compilation="Vol6", video="C:/v/clip.mp4")


class TestTheRecord:
    def test_the_record_it_writes_is_the_one_it_reads_back(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"

        ModeMemory(path).write(RememberedMode(compilation="Volume Six", video="C:/x/y.mp4"))

        written = path.read_text(encoding="utf-8")
        assert written.splitlines() == ["compilation=Volume Six", "video=C:/x/y.mp4"]
        assert written.endswith("\n"), "the last record is terminated like the others"

    def test_a_record_from_before_fun_time_kept_the_length_reads_its_compilation(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        path.write_text("length_mode=clips\ncompilation=Vol6\nvideo=C:/v/clip.mp4\n",
                        encoding="utf-8")

        assert ModeMemory(path).read() == INSIDE

    def test_nothing_remembered_reads_empty(self, tmp_path):
        assert ModeMemory(tmp_path / "never_written.txt").read() == RememberedMode()

    def test_without_a_path_it_is_inert(self):
        memory = ModeMemory(None)

        memory.write(INSIDE)

        assert memory.read() == RememberedMode()


class TestWritingItDownWheneverItMoves:
    def test_a_record_that_has_not_moved_is_not_written_again(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        memory = ModeMemory(path)
        memory.write(INSIDE)
        path.unlink()

        memory.sync(INSIDE)

        assert not path.exists()

    def test_a_record_that_moved_is_written_down(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        memory = ModeMemory(path)
        memory.write(INSIDE)

        memory.sync(RememberedMode())

        assert memory.read() == RememberedMode()

    def test_a_record_it_wrote_itself_is_not_written_again_either(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        memory = ModeMemory(path)
        memory.sync(INSIDE)
        path.unlink()

        memory.sync(INSIDE)

        assert not path.exists()

    def test_what_was_read_back_at_startup_counts_as_already_written(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        ModeMemory(path).write(INSIDE)
        memory = ModeMemory(path)
        remembered = memory.read()
        path.unlink()

        memory.sync(remembered)

        assert not path.exists()


class TestCompilation:
    def test_the_compilation_and_its_video_read_back(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"

        ModeMemory(path).write(INSIDE)

        assert ModeMemory(path).read() == INSIDE

    def test_a_title_may_hold_anything_but_a_newline_the_separator_included(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        title = "various - Example Studio Alpha Collection = Volume 6 (v1) = remastered"

        ModeMemory(path).write(RememberedMode(compilation=title))

        assert ModeMemory(path).read().compilation == title

    def test_leaving_a_compilation_is_remembered_too(self, tmp_path):
        path = tmp_path / "main_player_mode.txt"
        memory = ModeMemory(path)
        memory.write(INSIDE)

        memory.write(RememberedMode())

        assert memory.read().compilation == ""


class TestWhenTheFileCannotBeRead:
    def test_a_file_it_cannot_open_reads_as_nothing_remembered(self, tmp_path, monkeypatch):
        path = tmp_path / "main_player_mode.txt"
        ModeMemory(path).write(INSIDE)

        def refuse(*_args, **_kwargs):
            raise OSError("held open by something else")
        monkeypatch.setattr(Path, "read_text", refuse)

        assert ModeMemory(path).read() == RememberedMode()
