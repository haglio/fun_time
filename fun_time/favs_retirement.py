"""favs.csv's retirement: the pages it lists carried onto the records that now
say which videos are favorites, and the file deleted once the Evolver whose
contract is named leaves that mark to the apps that set it.

Run once on this machine, as ``python -m fun_time.favs_retirement <favs.csv>
<records folder> <evolver_contract.json>``; it prints counts and no path."""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

from app_support import favorites

from .favs_csv import hyperlink_parts
from .media_metadata import favorites_in, mark_favorite, normalize_path_key, recorded_for

_TARGET, _DISPLAY = 0, 1


def _cell(cell: str, part: int) -> str:
    cell = cell.strip()
    parts = hyperlink_parts(cell)
    if parts is None:
        return "" if cell.startswith('=HYPERLINK("') else cell
    return parts[part]


def listed_favorites(favs_file: Path) -> list[tuple[str, str]]:
    with favs_file.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    listed = ((_cell(row.get("local_file", ""), _DISPLAY), _cell(row.get("web_url", ""), _TARGET))
              for row in rows)
    return [(video, page) for video, page in listed if video]


def evolver_leaves_the_favorite_to_its_readers(contract: Path) -> bool:
    try:
        record = json.loads(contract.read_text(encoding="utf-8"))["library_record"]
        return favorites.FAVORITE_KEY in record.get("top_level_written_by_the_reader", ())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


@dataclass(frozen=True)
class Retirement:
    rows: int
    marked: int
    unmarked: int
    marked_unlisted: int
    pages_carried: int
    deleted: bool


def retire(favs_file: Path, metadata_root: Path, evolver_contract: Path) -> Retirement:
    listed = listed_favorites(favs_file)
    marked = carried = 0
    for video, page in listed:
        record = recorded_for(video, metadata_root)
        if not favorites.is_favorite(record):
            continue
        marked += 1
        if page and not favorites.page_of(record):
            carried += mark_favorite(video, metadata_root, True, page=page)
    named = {normalize_path_key(video) for video, _page in listed}
    unlisted = sum(normalize_path_key(str(favorite.video)) not in named
                   for favorite in favorites_in(metadata_root))
    deleted = evolver_leaves_the_favorite_to_its_readers(evolver_contract)
    if deleted:
        favs_file.unlink()
    return Retirement(rows=len(listed), marked=marked, unmarked=len(listed) - marked,
                      marked_unlisted=unlisted, pages_carried=carried, deleted=deleted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fun_time.favs_retirement")
    for name in ("favs_file", "metadata_root", "evolver_contract"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    done = retire(args.favs_file, args.metadata_root, args.evolver_contract)
    print(f"{done.rows} row(s); {done.marked} marked a favorite on their record, "
          f"{done.unmarked} not; {done.marked_unlisted} favorite(s) on a record and in no row; "
          f"{done.pages_carried} page(s) carried onto a record; favs.csv "
          + ("deleted" if done.deleted else "kept: that Evolver still copies it onto the records"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
