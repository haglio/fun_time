from __future__ import annotations

from pathlib import Path


FORMULA_SEP = ";"


def csv_escape(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def ensure_favs_csv_exists(favs_file: Path) -> None:
    favs_file.parent.mkdir(parents=True, exist_ok=True)
    if favs_file.exists() and favs_file.stat().st_size > 0:
        return
    with favs_file.open("w", encoding="utf-8", newline="") as fp:
        fp.write("local_file,web_url\r\n")


def to_file_uri(win_path: str) -> str:
    if not win_path:
        return ""
    return "file:///" + win_path.replace("\\", "/").replace(" ", "%20")


def make_web_url_from_path(full_path: str) -> str:
    if not full_path:
        return ""

    path = full_path.replace("/", "\\")
    name_no_ext = Path(path).stem
    image_id = name_no_ext.rsplit("_", 1)[0] if "_" in name_no_ext else name_no_ext
    lower_path = path.lower()

    if "\\provider2\\" in lower_path:
        return f"https://example.net/image/{image_id}"
    if "\\provider\\" in lower_path:
        return f"https://example.com/image/{image_id}"
    return ""


def make_local_cell(full_path: str) -> str:
    if not full_path:
        return ""
    uri = to_file_uri(full_path)
    return f'=HYPERLINK("{uri}"{FORMULA_SEP}"{full_path}")'


def make_web_cell(full_path: str) -> str:
    url = make_web_url_from_path(full_path)
    if not url:
        return ""
    return f'=HYPERLINK("{url}"{FORMULA_SEP}"{url}")'


def ensure_in_favs(favs_file: Path, full_path: str) -> None:
    if not full_path:
        return

    ensure_favs_csv_exists(favs_file)
    local_cell = make_local_cell(full_path)
    web_cell = make_web_cell(full_path)
    content = favs_file.read_text(encoding="utf-8")
    prefix = csv_escape(local_cell) + ","
    if prefix in content:
        return
    row = prefix + csv_escape(web_cell) + "\r\n"
    with favs_file.open("a", encoding="utf-8", newline="") as fp:
        fp.write(row)


def remove_from_favs(favs_file: Path, full_path: str) -> None:
    if not full_path or not favs_file.exists():
        return

    target_prefix = csv_escape(make_local_cell(full_path)) + ","
    kept_lines: list[str] = []
    for raw_line in favs_file.read_text(encoding="utf-8").splitlines():
        if not raw_line:
            continue
        if raw_line.startswith("local_file,web_url"):
            kept_lines.append(raw_line)
            continue
        if raw_line.startswith(target_prefix):
            continue
        kept_lines.append(raw_line)
    with favs_file.open("w", encoding="utf-8", newline="") as fp:
        fp.write("".join(line + "\r\n" for line in kept_lines))


def move_to_weird(weird_dir: Path, source: Path, *, destination_name: str | None = None) -> Path:
    weird_dir.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return weird_dir / (destination_name or source.name)

    target_name = destination_name or source.name
    destination = weird_dir / target_name
    stem = Path(target_name).stem
    suffix = Path(target_name).suffix
    duplicate_index = 1
    while destination.exists():
        destination = weird_dir / f"{stem}__dup{duplicate_index}{suffix}"
        duplicate_index += 1
    source.replace(destination)
    return destination


def run_media_action(action: str, *, favs_file: Path, weird_dir: Path, path: str) -> str:
    if action == "ensure-in-favs":
        ensure_in_favs(favs_file, path)
        return "added-to-favs"
    if action == "remove-from-favs":
        remove_from_favs(favs_file, path)
        return "removed-from-favs"
    if action == "move-to-weird":
        move_to_weird(weird_dir, Path(path))
        return "moved-to-weird"
    raise ValueError(f"Unsupported media action: {action}")
