from __future__ import annotations

import fnmatch
import json
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app_support.file_channel import write_whole
from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.checkout_overrides import STATE_DIRNAME, primary_of

INTEGRATION_TESTS = "tests/integration/test_*.py"
NEVER_SHIPPED = ("tests/*", "docs/*", "*.md", ".github/*", ".claude/*", "tools/*")
MAP_NAME = "integration_coverage_map.json"


def the_machine_s_map(checkout: Path) -> Path:
    return primary_of(checkout) / STATE_DIRNAME / MAP_NAME


@dataclass(frozen=True)
class CoverageMap:
    commit: str
    ran: Mapping[str, frozenset[str]]

    def tests_that_ran(self, source: str) -> set[str]:
        return {test for test, sources in self.ran.items() if source in sources}

    def save(self, path: Path) -> None:
        write_whole(path, json.dumps({"commit": self.commit,
                                      "ran": {test: sorted(sources) for test, sources in sorted(self.ran.items())}},
                                     indent=1))

    @classmethod
    def load(cls, path: Path) -> CoverageMap | None:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cls(saved["commit"], {test: frozenset(sources) for test, sources in saved["ran"].items()})


@dataclass(frozen=True)
class Picked:
    files: frozenset[str] = field(default_factory=frozenset)
    because: str | None = None

    @property
    def everything(self) -> bool:
        return self.because is not None

    def reason(self) -> str:
        if self.everything:
            return f"running the whole integration suite, because the coverage map cannot say which tests run {self.because}"
        if not self.files:
            return "no integration test runs what this change touched"
        return f"running the integration tests that run what this change touched: {', '.join(sorted(self.files))}"


def tests_to_run(changed: Iterable[str], coverage: CoverageMap | None) -> Picked:
    files = set()
    for path in changed:
        if fnmatch.fnmatch(path, INTEGRATION_TESTS):
            files.add(path)
        elif _never_shipped(path) and not fnmatch.fnmatch(path, "tests/integration/*"):
            continue
        elif ran_it := coverage.tests_that_ran(path) if coverage else set():
            files |= ran_it
        else:
            return Picked(because=path)
    return Picked(files=frozenset(files))


def source_files(tracked: Iterable[str]) -> frozenset[str]:
    return frozenset(path for path in tracked if path.endswith(".py") and not _never_shipped(path))


def _never_shipped(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in NEVER_SHIPPED)


def changed_paths(checkout: Path, base: str) -> list[str]:
    since = _git(checkout, "merge-base", base, "HEAD").strip()
    edited = _git(checkout, "diff", "--name-only", since).splitlines()
    added = _git(checkout, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({*edited, *added})


def _git(checkout: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", **hidden_subprocess_kwargs()).stdout
