"""Bringing a reopened session back to the clip each player was on."""
from __future__ import annotations

from pathlib import Path

from fun_time.session_resume import resume_playlists


def _clips(tmp_path: Path, *names: str) -> list[str]:
    """Real files on disk, since a playlist names videos that have to be there."""
    paths = []
    for name in names:
        clip = tmp_path / name
        clip.write_bytes(b"")
        paths.append(str(clip))
    return paths


def _write_playlist(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


class TestResumePlaylists:
    def test_rotates_a_playlist_onto_the_clip_that_was_on_screen(self, tmp_path: Path):
        """A player starts at the top of its playlist file, so putting last
        session's clip there is what makes it resume — and rotating (rather than
        moving it to the front) keeps the clips that were coming up coming up."""
        a, b, c = _clips(tmp_path, "a.mp4", "b.mp4", "c.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
        _write_playlist(playlist, [a, b, c])

        assert resume_playlists([(playlist, b)]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [b, c, a]

    def test_carries_the_funscript_column_through_the_rotation(self, tmp_path: Path):
        """Nau's playlist pairs each video with the funscript that drives the
        OSR2 through it; rewriting the file without that column would leave a
        resumed session silently unscripted."""
        a, b, c = _clips(tmp_path, "a.mp4", "b.mp4", "c.mp4")
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [f"{a}\ta.funscript", b, f"{c}\tc.funscript"])

        resume_playlists([(playlist, c)])

        assert playlist.read_text(encoding="utf-8").splitlines() == [
            f"{c}\tc.funscript",
            f"{a}\ta.funscript",
            b,
        ]

    def test_reports_nothing_to_resume_when_a_playlist_file_is_missing(self, tmp_path: Path):
        """A first run, or a state dir that has been wiped: there is no last
        session on disk, so the caller has to build the playlists fresh."""
        assert resume_playlists([(tmp_path / "portrait_playlist.tsv", "")]) is False

    def test_leaves_every_playlist_alone_when_one_of_them_cannot_be_resumed(self, tmp_path: Path):
        """One build writes all three playlists, so they live or die together:
        if any of them has no session to come back to, the caller rebuilds the
        set and each file must still be as the build left it."""
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        portrait = tmp_path / "portrait_playlist.tsv"
        _write_playlist(portrait, [a, b])

        resumed = resume_playlists([
            (portrait, b),
            (tmp_path / "nau_playlist.tsv", ""),
        ])

        assert resumed is False
        assert portrait.read_text(encoding="utf-8").splitlines() == [a, b]

    def test_keeps_the_queue_when_the_clip_it_left_on_is_gone(self, tmp_path: Path):
        """A clip trashed or deleted since leaves nothing to rotate onto, but
        the queue around it is still last session's — worth far more than a
        fresh shuffle — so it comes back from its top."""
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "landscape_playlist.tsv"
        _write_playlist(playlist, [a, b])

        assert resume_playlists([(playlist, str(tmp_path / "deleted.mp4"))]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [a, b]

    def test_drops_clips_that_no_longer_exist(self, tmp_path: Path):
        """A playlist built moments before launch could only name files that were
        there; one resumed from yesterday can name files trashed since, and mpv
        cannot load those — so they come out as the file is rewritten."""
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        gone = str(tmp_path / "gone.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
        _write_playlist(playlist, [a, gone, b])

        assert resume_playlists([(playlist, b)]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [b, a]
