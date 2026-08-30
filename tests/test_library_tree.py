"""The folders the library browser walks — and the ones it refuses to."""
from __future__ import annotations

import random

from fun_time.library_handles import LibraryHandle
from fun_time.library_tree import folder_at


def _handle(title: str, section: str) -> LibraryHandle:
    return LibraryHandle(title=title, versions=(f"C:/videos/{title}.mp4",), section=section)


def test_the_root_lists_the_libraries_own_folders():
    """One tile per source folder, biggest first — nothing below it yet."""
    folder = folder_at([
        _handle("Beta Scene", "big_batch/whole"),
        _handle("Excerpt 1", "big_batch/cuts"),
        _handle("Excerpt 2", "big_batch/cuts"),
        _handle("alpha scene", "small_batch"),
    ], ())

    assert [(child.name, child.count) for child in folder.children] == [
        ("big_batch", 3), ("small_batch", 1),
    ]
    assert folder.handles == ()
    assert folder.path == ()


def test_opening_a_folder_that_splits_shows_the_two_it_was_split_into():
    handles = [
        _handle("Beta Scene", "big_batch/whole"),
        _handle("Excerpt 1", "big_batch/cuts"),
        _handle("Excerpt 2", "big_batch/cuts"),
    ]

    folder = folder_at(handles, ("big_batch",))

    assert [(child.name, child.count) for child in folder.children] == [
        ("whole", 1), ("cuts", 2),
    ]
    assert folder.handles == ()


def test_opening_a_folder_that_does_not_split_shows_its_videos():
    handles = [_handle("alpha scene", "small_batch"), _handle("beta scene", "small_batch")]

    folder = folder_at(handles, ("small_batch",))

    assert folder.children == ()
    assert [handle.title for handle in folder.handles] == ["alpha scene", "beta scene"]


def test_the_deepest_folder_shows_every_video_under_it_at_once():
    """No pipeline stage is ever a step: a section's videos are one flat grid,
    however many processing folders they are spread over on disk."""
    handles = [_handle(f"Excerpt {i}", "big_batch/cuts") for i in range(3)]

    folder = folder_at(handles, ("big_batch", "cuts"))

    assert folder.children == ()
    assert [handle.title for handle in folder.handles] == ["Excerpt 0", "Excerpt 1", "Excerpt 2"]


def test_a_folder_carries_the_way_back_up():
    handles = [_handle("Excerpt 1", "big_batch/cuts")]

    assert folder_at(handles, ("big_batch", "cuts")).parent == ("big_batch",)
    assert folder_at(handles, ("big_batch",)).parent == ()
    assert folder_at(handles, ()).parent is None




def test_a_folder_pictures_itself_with_four_of_its_videos():
    """One still says almost nothing about a folder of hundreds.

    Four, drawn at random, say what kind of thing is in there — and change from
    browse to browse, so a folder is never represented by the same picture twice
    running.
    """
    import random

    handles = [_handle(f"Scene {i}", "big_batch") for i in range(10)]

    child = folder_at(handles, (), rng=random.Random(7)).children[0]
    other = folder_at(handles, (), rng=random.Random(8)).children[0]

    assert len(child.previews) == 4
    assert len(set(child.previews)) == 4, "the same video must not fill two cells"
    assert set(child.previews) <= {handle.preview for handle in handles}
    assert child.previews != other.previews, "a different browse picks differently"


def test_a_folder_with_less_than_four_pictures_itself_with_what_it_has():
    handles = [_handle("Beta Scene", "big_batch"), _handle("Gamma Scene", "big_batch")]

    child = folder_at(handles, ()).children[0]

    assert set(child.previews) == {handle.preview for handle in handles}


class TestAFolderTileHoldsStill:
    """The four stills a tile is drawn with are sampled from its members.  With
    a fresh Random each call, walking into a folder and straight back up redrew
    the parent with a different set — the tile changed under the pointer that
    had just been on it."""

    @staticmethod
    def _handles(folder: str = "big_batch", count: int = 12):
        return [_handle(f"scene {n}", f"{folder}/whole") for n in range(count)]

    def test_the_same_folder_draws_the_same_stills_every_visit(self):
        handles = self._handles()

        first = folder_at(handles, ())
        second = folder_at(handles, ())

        assert [child.previews for child in first.children] == [
            child.previews for child in second.children]

    def test_two_folders_do_not_all_draw_the_same_four(self):
        """Stable, not identical: each tile is seeded from its own name."""
        handles = self._handles("one") + self._handles("two")

        one, two = folder_at(handles, ()).children[:2]

        assert one.previews != two.previews

    def test_a_test_can_still_pin_them(self):
        handles = self._handles()

        assert folder_at(handles, (), rng=random.Random(7)).children[0].previews == (
            folder_at(handles, (), rng=random.Random(7)).children[0].previews)
