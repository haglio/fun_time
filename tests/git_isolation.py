from __future__ import annotations

REPOSITORY_VARIABLES = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
})


def forget_the_enclosing_repository(environ) -> None:
    for name in REPOSITORY_VARIABLES:
        environ.pop(name, None)
