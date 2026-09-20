"""The last listing of the library, kept so the next browse opens on it at once."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time_vr.library_listing_cache import remembered_listing


@pytest.fixture
def kept(tmp_path) -> Path:
    return tmp_path / "library_listing.json"


def _a_listing(*videos: str) -> list[str]:
    return list(videos)


class TestTheFirstTime:
    def test_nothing_is_kept_yet_so_the_read_itself_answers(self, kept):
        read = lambda: _a_listing("a.mp4", "b.mp4")  # noqa: E731

        assert remembered_listing(kept, read) == ["a.mp4", "b.mp4"]

    def test_and_what_it_found_is_kept_for_next_time(self, kept):
        remembered_listing(kept, lambda: _a_listing("a.mp4"))

        assert json.loads(kept.read_text(encoding="utf-8"))["videos"] == ["a.mp4"]


class TestEveryTimeAfter:
    def test_what_was_kept_answers_without_reading_the_drive_again(self, kept):
        remembered_listing(kept, lambda: _a_listing("a.mp4", "b.mp4"))
        reads = 0

        def never_reached():
            nonlocal reads
            reads += 1
            return []

        assert remembered_listing(kept, never_reached) == ["a.mp4", "b.mp4"]
        assert reads == 0

    def test_a_fresh_read_replaces_it(self, kept):
        remembered_listing(kept, lambda: _a_listing("a.mp4"))

        assert remembered_listing(kept, lambda: _a_listing("b.mp4"), afresh=True) == ["b.mp4"]
        assert remembered_listing(kept, list) == ["b.mp4"]


class TestWhatIsNotTrusted:
    def test_a_kept_listing_that_is_not_a_listing_at_all(self, kept):
        kept.write_text(json.dumps({"videos": "not a list"}), encoding="utf-8")

        assert remembered_listing(kept, lambda: _a_listing("a.mp4")) == ["a.mp4"]

    def test_a_file_that_cannot_be_read_as_a_record(self, kept):
        kept.write_text("{ not json", encoding="utf-8")

        assert remembered_listing(kept, lambda: _a_listing("a.mp4")) == ["a.mp4"]

    def test_an_empty_read_is_not_kept_over_what_was_there(self, kept):
        """An unreachable drive lists as nothing, and keeping that would lose the
        library until the next read happened to succeed."""
        remembered_listing(kept, lambda: _a_listing("a.mp4"))

        assert remembered_listing(kept, list, afresh=True) == []
        assert json.loads(kept.read_text(encoding="utf-8"))["videos"] == ["a.mp4"]

    def test_a_place_it_cannot_write_is_not_a_failure_to_browse(self, tmp_path):
        unwritable = tmp_path / "no such folder" / "deeper still" / "listing.json"

        assert remembered_listing(unwritable, lambda: _a_listing("a.mp4")) == ["a.mp4"]
