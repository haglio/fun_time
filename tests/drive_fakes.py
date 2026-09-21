"""A library drive that lists its folders but never answers about a file.

pCloud's drive spent nineteen hours in that state on 2026-09-20: it listed the
VR folder in a millisecond and never came back from a question about one video
in it, so every control in the headset died the moment a rebuild asked.  A test
cannot wait hours to find that out, so here the question raises instead --
and out of :class:`BaseException`, since the point is that the caller gets no
answer it could have caught and worked around.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch


class DriveNeverAnswered(BaseException):
    """Raised where the drive would hang for hours instead."""


_QUESTIONS = (
    (os, "stat"), (os, "lstat"),
    (os.path, "isfile"), (os.path, "isdir"), (os.path, "islink"),
    (os.path, "exists"), (os.path, "lexists"),
    (os.path, "getmtime"), (os.path, "getsize"), (os.path, "getctime"),
    (os.path, "realpath"),
)


@contextmanager
def files_that_never_answer(folder: Path) -> Iterator[None]:
    """Within the block, any question about a file under *folder* never answers.

    Listing the folder itself keeps working, which is how the real drive
    behaves -- and is the only way anything can still be read off it.
    """
    silent = {
        os.path.normcase(str(path)) for path in folder.rglob("*") if path.is_file()
    }

    def never(real):
        def asked(path, *args, **kwargs):
            try:
                spelled = os.path.normcase(os.fspath(path))
            except TypeError:
                spelled = ""
            if spelled in silent:
                raise DriveNeverAnswered(spelled)
            return real(path, *args, **kwargs)

        return asked

    with ExitStack() as stack:
        for module, name in _QUESTIONS:
            stack.enter_context(patch.object(module, name, never(getattr(module, name))))
        yield


@contextmanager
def folders_listed() -> Iterator[list[str]]:
    """Collects the folders listed inside the block, one entry per listing.

    A listing is the one question the drive always answers, so what matters is
    how many of them a drawing takes -- one per folder, however many videos.
    """
    read: list[str] = []
    real = os.scandir

    def listing(where="."):
        read.append(str(where))
        return real(where)

    with patch.object(os, "scandir", listing):
        yield read
