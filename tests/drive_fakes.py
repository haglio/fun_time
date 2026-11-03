from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch


class DriveNeverAnswered(BaseException):
    pass


_QUESTIONS = (
    (os, "stat"), (os, "lstat"),
    (os.path, "isfile"), (os.path, "isdir"), (os.path, "islink"),
    (os.path, "exists"), (os.path, "lexists"),
    (os.path, "getmtime"), (os.path, "getsize"), (os.path, "getctime"),
    (os.path, "realpath"),
)


@contextmanager
def files_that_never_answer(folder: Path) -> Iterator[None]:
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
    read: list[str] = []
    real = os.scandir

    def listing(where="."):
        read.append(str(where))
        return real(where)

    with patch.object(os, "scandir", listing):
        yield read
