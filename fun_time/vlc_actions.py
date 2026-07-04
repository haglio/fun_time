from __future__ import annotations

import base64
import logging
import html
import re
from urllib.parse import unquote, urlparse
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


def restore_vlcrc_volume(volume: int = 256) -> None:
    """Write volume setting directly to vlcrc on disk.

    Call AFTER killing VLC to prevent muted state from persisting across
    sessions.  Unlike restore_vlc_volume (HTTP), this never produces
    audible output because VLC is already dead.
    """
    import os
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    vlcrc = Path(appdata) / "vlc" / "vlcrc"
    try:
        text = vlcrc.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    new_text = re.sub(r"^volume=\d+", f"volume={volume}", text, flags=re.MULTILINE)
    if new_text != text:
        try:
            vlcrc.write_text(new_text, encoding="utf-8")
        except OSError:
            pass


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


def get_playback_time_and_length(port: int, password: str) -> tuple[float, float] | None:
    """Return VLC's ``(time, length)`` in seconds, or None if time is unavailable.

    ``length`` is 0.0 when VLC does not report one (streams / unprobed media);
    callers treat 0 as "unknown", not "zero-length".
    """
    status, xml = vlc_http_req(port, "/requests/status.xml", password)
    if status != 200 or not xml:
        return None
    time_match = re.search(r"<time>([^<]+)</time>", xml)
    if not time_match:
        return None
    try:
        time_s = float(time_match.group(1))
    except ValueError:
        return None
    length_s = 0.0
    length_match = re.search(r"<length>([^<]+)</length>", xml)
    if length_match:
        try:
            length_s = float(length_match.group(1))
        except ValueError:
            length_s = 0.0
    return time_s, length_s


def get_playback_time(port: int, password: str) -> float | None:
    """Return VLC's current playback position in seconds, or None on error."""
    pos = get_playback_time_and_length(port, password)
    return pos[0] if pos else None


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
        if state == "stopped":
            if not should_play:
                # Stopped already satisfies "not playing".  pl_pause on a
                # stopped VLC STARTS the current item (toggle semantics),
                # phantom-loading item 1 — never send it from here.
                return True
            vlc_http_cmd(port, "pl_play", password)
        else:
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


def _normalize_media_path(path: str) -> str:
    return path.replace("/", "\\").casefold().rstrip("\\")


def _find_item_id_by_path(xml: str, video_path: str) -> int:
    """Resolve a playlist item id whose file URI matches *video_path*.

    Returns -1 when no item matches.  Comparison is slash- and
    case-insensitive so an OS path from Nau's status file matches the
    percent-encoded file:/// URI VLC reports.
    """
    want = _normalize_media_path(video_path)
    for attrs in re.findall(r'<item\b([^>]*)>', xml):
        uri_m = re.search(r'\buri="([^"]*)"', attrs)
        id_m = re.search(r'\bid="plid_(\d+)"', attrs)
        if not uri_m or not id_m:
            continue
        parsed = urlparse(html.unescape(uri_m.group(1)))
        if parsed.scheme != "file":
            continue
        item_path = unquote(parsed.path)
        # file:///C:/x → path "/C:/x"; drop the leading slash before the drive.
        if re.match(r"/[A-Za-z]:", item_path):
            item_path = item_path[1:]
        if _normalize_media_path(item_path) == want:
            return int(id_m.group(1))
    return -1


def play_item_at(
    port: int,
    password: str,
    video_path: str,
    seconds: float,
    *,
    sleep_fn=time.sleep,
) -> bool:
    """Play the playlist item matching *video_path*, seeking to *seconds*.

    The hybrid-entry handoff: the primary VLC picks up the video (and
    position) Nau was playing.  Returns False when the video is not in
    VLC's playlist so the caller can fall back to plain resume.
    """
    status, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", password)
    if status != 200 or not xml:
        return False
    item_id = _find_item_id_by_path(xml, video_path)
    if item_id < 0:
        return False
    if not vlc_http_cmd(port, f"pl_play&id={item_id}", password):
        return False
    target = int(seconds)
    if target <= 0:
        return True
    # Seeks sent before the item finishes opening are silently dropped —
    # wait for the input to report playing before positioning it.
    for _ in range(10):
        if get_playback_state(port, password) == "playing":
            break
        sleep_fn(0.15)
    vlc_http_cmd(port, f"seek&val={target}", password)
    return True


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


def vlc_nav_step(port: int, password: str, direction: str) -> bool:
    """Navigate to the previous or next playlist item via pl_play&id=N.

    Unlike pl_previous/pl_next, this bypasses VLC's restart-threshold
    behavior (where pl_previous restarts the current track if you are
    more than a few seconds in).  The target item is resolved from the
    live playlist and played directly by ID.

    Returns True only if the item actually changed — pl_play&id can be
    silently ignored by VLC when jstree IDs are stale (e.g. after a
    playlist wrap).  Retries once with fresh IDs if the first attempt
    produces no change within 2 seconds.

    direction: "prev" or "next"
    """
    for attempt in range(2):
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
        logger.info(
            "vlc_nav_step port=%s dir=%s attempt=%d playlist_len=%d idx=%d/%d current_id=%d target_id=%d",
            port, direction, attempt, len(all_ids), idx, len(all_ids) - 1, current_id, target_id,
        )
        current_path = get_current_file_path(port, password)
        if not vlc_http_cmd(port, f"pl_play&id={target_id}", password):
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if get_current_file_path(port, password) != current_path:
                return True
            time.sleep(0.05)
        logger.warning(
            "vlc_nav_step: no item change after pl_play&id=%d (attempt %d), %s",
            target_id, attempt, "retrying with fresh jstree" if attempt == 0 else "giving up",
        )
        time.sleep(0.1)
    return False


def vlc_advance_and_remove(
    port: int,
    password: str,
    *,
    sleep_fn=time.sleep,
) -> bool:
    """Advance to the next playlist item and remove the current one.

    Used during discard: plays the next item by explicit ID (not pl_next,
    which is unreliable after manual pl_play navigation), then deletes
    the discarded item from VLC's playlist so it can't be navigated to.

    A short delay between play and delete gives VLC time to transition
    to the new item before the old one is removed, preventing VLC from
    entering a stopped/black-screen state.

    For a single-item playlist, only the delete is issued (no advance).
    """
    status, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", password)
    if status != 200 or not xml:
        return False
    all_ids, current_id = _parse_playlist_ids(xml)
    if not all_ids or current_id < 0 or current_id not in all_ids:
        logger.warning("vlc_advance_and_remove: could not resolve playlist position")
        return False
    idx = all_ids.index(current_id)
    if len(all_ids) > 1:
        next_id = all_ids[(idx + 1) % len(all_ids)]
        vlc_http_cmd(port, f"pl_play&id={next_id}", password)
        sleep_fn(0.15)
    vlc_http_cmd(port, f"pl_delete&id={current_id}", password)
    return True


def replace_playlist_from_file(
    port: int,
    password: str,
    playlist_path: str | Path,
    *,
    repeat_mode: str = "",
    enqueue_only: bool = False,
    sleep_fn=time.sleep,
) -> bool:
    playlist = Path(playlist_path)
    if not playlist.is_file():
        return False

    vlc_http_cmd(port, "pl_empty", password)
    vlc_http_cmd(port, "pl_stop", password)
    sleep_fn(0.18)

    input_cmd = "in_enqueue" if enqueue_only else "in_play"
    if not send_vlc_input_command(port, input_cmd, str(playlist), password):
        return False

    if repeat_mode:
        return set_repeat_mode(port, password, repeat_mode, sleep_fn=sleep_fn)
    return True
