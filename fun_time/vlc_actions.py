from __future__ import annotations

import base64
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


def vlc_http_req(port: int, path: str, password: str, user: str = "") -> tuple[int, str]:
    url = f"http://127.0.0.1:{port}{path}"
    credentials = f"{user}:{password}".encode("utf-8")
    auth = "Basic " + base64.b64encode(credentials).decode("ascii")
    request = urllib.request.Request(url, headers={"Authorization": auth})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = getattr(response, "status", 200)
            return status, response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("vlc_http_req port=%s path=%s error=%r", port, path, exc)
        return 0, ""


def wait_for_http(
    port: int,
    password: str,
    timeout_ms: int = 5000,
    *,
    sleep_fn=time.sleep,
) -> bool:
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    while time.monotonic() <= deadline:
        status, xml = vlc_http_req(port, "/requests/status.xml", password)
        if status == 200 and "<state>" in xml:
            return True
        sleep_fn(0.2)
    return False


def vlc_http_cmd(port: int, command: str, password: str) -> bool:
    status, _ = vlc_http_req(port, f"/requests/status.xml?command={command}", password)
    return status == 200


def send_vlc_input_command(port: int, command: str, full_path: str, password: str) -> bool:
    file_uri = Path(full_path).resolve().as_uri()
    encoded_uri = urllib.parse.quote(file_uri, safe="-_.~")
    status, _ = vlc_http_req(port, f"/requests/status.xml?command={command}&input={encoded_uri}", password)
    return status == 200


def decode_file_uri(uri: str) -> str:
    if not uri.startswith("file:///"):
        return ""
    decoded = urllib.parse.unquote(uri[8:])
    return decoded.replace("/", "\\")


def get_current_file_path(port: int, password: str) -> str:
    status, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", password)
    if status != 200 or not xml:
        return ""

    match = re.search(r'uri="([^"]+)"[^>]*current="current"', xml, re.IGNORECASE)
    if not match:
        match = re.search(r'current="current"[^>]*uri="([^"]+)"', xml, re.IGNORECASE)
    if not match:
        return ""
    return decode_file_uri(match.group(1))


def get_repeat_mode(port: int, password: str) -> str | None:
    status, xml = vlc_http_req(port, "/requests/status.xml", password)
    if status != 200 or not xml:
        return None

    loop_match = re.search(r"<loop>([^<]+)</loop>", xml)
    repeat_match = re.search(r"<repeat>([^<]+)</repeat>", xml)
    loop_val = (loop_match.group(1).strip().lower() if loop_match else "")
    repeat_val = (repeat_match.group(1).strip().lower() if repeat_match else "")

    if repeat_val in {"1", "true", "yes"}:
        return "one"
    if loop_val in {"1", "true", "yes"}:
        return "all"
    return "off"


def get_playback_time(port: int, password: str) -> float | None:
    """Return VLC's current playback position in seconds, or None on error."""
    status, xml = vlc_http_req(port, "/requests/status.xml", password)
    if status != 200 or not xml:
        return None
    match = re.search(r"<time>([^<]+)</time>", xml)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def get_playback_state(port: int, password: str) -> str | None:
    status, xml = vlc_http_req(port, "/requests/status.xml", password)
    if status != 200 or not xml:
        return None
    match = re.search(r"<state>([^<]+)</state>", xml)
    return match.group(1).strip().lower() if match else None


def ensure_playback_state(
    port: int,
    password: str,
    should_play: bool,
    *,
    sleep_fn=time.sleep,
) -> bool:
    target = "playing" if should_play else "paused"
    for _ in range(8):
        state = get_playback_state(port, password)
        if state is None:
            break
        if state == target:
            return True
        vlc_http_cmd(port, "pl_pause", password)
        sleep_fn(0.12)
    return False


def set_repeat_mode(
    port: int,
    password: str,
    target: str,
    *,
    sleep_fn=time.sleep,
) -> bool:
    for _ in range(12):
        current = get_repeat_mode(port, password)
        if current is None:
            return False
        if current == target:
            return True
        vlc_http_cmd(port, "pl_repeat" if target == "one" else "pl_loop", password)
        sleep_fn(0.12)
    return False


def _parse_playlist_ids(xml: str) -> tuple[list[int], int]:
    """Return (ordered_ids, current_id) from a playlist_jstree.xml response.

    Returns ([], -1) if the playlist cannot be parsed.

    VLC 3.x uses ``<item id="plid_N" uri="...">`` for media items.
    Container nodes (root node, "Playlist" folder) have no ``uri=`` attribute
    and must be excluded from the navigation sequence.  The numeric suffix N
    is what VLC's ``pl_play&id=N`` command expects.
    """
    all_ids: list[int] = []
    current_id = -1
    for attrs in re.findall(r'<item\b([^>]*)>', xml):
        if 'uri=' not in attrs:
            continue  # skip container nodes (no uri= means not a media item)
        id_m = re.search(r'\bid="plid_(\d+)"', attrs)
        if not id_m:
            continue
        item_id = int(id_m.group(1))
        all_ids.append(item_id)
        if 'current="current"' in attrs:
            current_id = item_id
    return all_ids, current_id


def vlc_advance_and_remove_current(port: int, password: str) -> int:
    """Advance to the next playlist item and remove the current one.

    Reads the playlist once, explicitly plays the next item via
    ``pl_play&id=N`` (so VLC has a definite current item), then deletes
    the old entry.  This avoids the race where ``pl_next`` + ``pl_delete``
    leaves VLC with no current item.

    Returns the deleted item's playlist ID on success, or -1 on failure.
    """
    status, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", password)
    if status != 200 or not xml:
        return -1
    all_ids, current_id = _parse_playlist_ids(xml)
    if not all_ids or current_id < 0 or current_id not in all_ids:
        return -1
    idx = all_ids.index(current_id)
    if len(all_ids) > 1:
        next_id = all_ids[(idx + 1) % len(all_ids)]
        vlc_http_cmd(port, f"pl_play&id={next_id}", password)
    vlc_http_cmd(port, f"pl_delete&id={current_id}", password)
    return current_id


def vlc_nav_step(port: int, password: str, direction: str) -> bool:
    """Navigate to the previous or next playlist item via pl_play&id=N.

    Unlike pl_previous/pl_next, this bypasses VLC's restart-threshold
    behavior (where pl_previous restarts the current track if you are
    more than a few seconds in).  The target item is resolved from the
    live playlist and played directly by ID.

    direction: "prev" or "next"
    """
    status, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", password)
    if status != 200 or not xml:
        return False
    all_ids, current_id = _parse_playlist_ids(xml)
    if not all_ids or current_id < 0 or current_id not in all_ids:
        logger.warning("vlc_nav_step: could not resolve playlist position (ids=%s current=%s)", all_ids, current_id)
        return False
    idx = all_ids.index(current_id)
    if direction == "prev":
        target_id = all_ids[(idx - 1) % len(all_ids)]
    else:
        target_id = all_ids[(idx + 1) % len(all_ids)]
    return vlc_http_cmd(port, f"pl_play&id={target_id}", password)


def replace_playlist_from_file(
    port: int,
    password: str,
    playlist_path: str | Path,
    *,
    repeat_mode: str = "",
    sleep_fn=time.sleep,
) -> bool:
    playlist = Path(playlist_path)
    if not playlist.is_file():
        return False

    vlc_http_cmd(port, "pl_empty", password)
    vlc_http_cmd(port, "pl_stop", password)
    sleep_fn(0.18)

    if not send_vlc_input_command(port, "in_play", str(playlist), password):
        return False

    if repeat_mode:
        return set_repeat_mode(port, password, repeat_mode, sleep_fn=sleep_fn)
    return True
