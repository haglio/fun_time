from __future__ import annotations

import base64
import http.client
import logging
import re
import threading
import time
import urllib.parse
from pathlib import Path

from .media_metadata import normalize_path_key

logger = logging.getLogger(__name__)

_VLC_HOST = "127.0.0.1"
# One keep-alive connection per (thread, port).  VLC's httpd closes every
# connection it serves, so opening a fresh socket per call floods the port with
# server-side TIME_WAIT sockets; under the rapid polling the navigation paths do
# they pile up (measured: 1331 for 1500 calls) and collide on ephemeral ports,
# stalling new connects until VLC's HTTP interface returns nothing at all.
# Reusing one connection per port keeps the socket count flat.  Thread-local so
# concurrent callers never share a socket's protocol state.
_conn_pool = threading.local()


def _get_pooled_conn(port: int) -> tuple[http.client.HTTPConnection, bool]:
    """Return (connection, was_fresh) for *port* from this thread's pool.

    ``was_fresh`` is True when the connection was just created, letting callers
    tell a genuine "VLC unreachable" failure (a fresh connect fails) apart from
    a stale keep-alive socket (a pooled connection VLC closed while idle).
    """
    pool = getattr(_conn_pool, "by_port", None)
    if pool is None:
        pool = _conn_pool.by_port = {}
    conn = pool.get(port)
    if conn is not None:
        return conn, False
    conn = pool[port] = http.client.HTTPConnection(_VLC_HOST, port, timeout=5)
    return conn, True


def _issue_request(
    conn: http.client.HTTPConnection, path: str, headers: dict[str, str]
) -> tuple[int, str]:
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    status = getattr(response, "status", 200)
    return status, response.read().decode("utf-8", errors="replace")


def _drop_pooled_conn(port: int, conn: http.client.HTTPConnection) -> None:
    try:
        conn.close()
    except Exception:
        pass
    pool = getattr(_conn_pool, "by_port", None)
    if pool is not None and pool.get(port) is conn:
        del pool[port]


def vlc_http_req(port: int, path: str, password: str, user: str = "") -> tuple[int, str]:
    credentials = f"{user}:{password}".encode("utf-8")
    headers = {"Authorization": "Basic " + base64.b64encode(credentials).decode("ascii")}
    conn, was_fresh = _get_pooled_conn(port)
    try:
        return _issue_request(conn, path, headers)
    except Exception as exc:
        _drop_pooled_conn(port, conn)
        if was_fresh:
            # A brand-new connection failed → VLC is unreachable.  Keep the
            # original single-attempt contract: callers read (0, "") as "down".
            logger.debug("vlc_http_req port=%s path=%s error=%r", port, path, exc)
            return 0, ""
    # The failure was on a REUSED connection VLC had closed while idle, so the
    # request never reached it.  Reconnect once and re-issue — transparent
    # keep-alive recovery, safe even for non-idempotent commands because the
    # command was not delivered on the dead socket.
    conn, _ = _get_pooled_conn(port)
    try:
        return _issue_request(conn, path, headers)
    except Exception as exc:
        logger.debug("vlc_http_req port=%s path=%s retry error=%r", port, path, exc)
        _drop_pooled_conn(port, conn)
        return 0, ""


def wait_for_http(
    port: int,
    password: str,
    timeout_ms: int = 5000,
    *,
    is_alive=None,
    sleep_fn=time.sleep,
) -> bool:
    """Poll VLC's HTTP interface until it answers, or the deadline passes.

    When *is_alive* is given it is called each iteration; if it returns False
    the wait aborts immediately (a VLC that has already exited will never bind
    its HTTP interface, so there is no point polling out the timeout).  This
    lets callers set a generous timeout to absorb slow binds under load while
    still failing fast on a genuine startup crash.
    """
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    while time.monotonic() <= deadline:
        status, xml = vlc_http_req(port, "/requests/status.xml", password)
        if status == 200 and "<state>" in xml:
            return True
        if is_alive is not None and not is_alive():
            return False
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


def get_playback_fraction(port: int, password: str) -> float | None:
    """How far through the current item playback is, 0..1 (None if unknown)."""
    status, xml = vlc_http_req(port, "/requests/status.xml", password)
    if status != 200 or not xml:
        return None
    match = re.search(r"<position>([^<]+)</position>", xml)
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
    last_state: str | None = None
    for _ in range(8):
        state = get_playback_state(port, password)
        if state is None:
            break
        last_state = state
        if state == target:
            return True
        if state == "stopped":
            if not should_play:
                # "stopped" does NOT yet satisfy "not playing": a VLC reports it
                # while mid-transition — still loading the item a nav just
                # selected — and starts PLAYING a moment later.  Returning
                # success here let OmniPause skip the pause entirely and the
                # satellite resumed on its own.  We still must never send
                # pl_pause to a stopped VLC (toggle semantics would START the
                # item, phantom-loading item 1), so keep watching instead and
                # pause it the instant it turns playing.
                sleep_fn(0.12)
                continue
            vlc_http_cmd(port, "pl_play", password)
        else:
            vlc_http_cmd(port, "pl_pause", password)
        sleep_fn(0.12)
    # A VLC that stayed stopped for the whole settle window is genuinely idle,
    # which does satisfy "not playing".
    return not should_play and last_state == "stopped"


def pause_if_playing(port: int, password: str) -> bool:
    """Pause a satellite only if it is actually playing; return whether it acted.

    The OmniPause watchdog calls this on every enforcement tick.  Unlike
    :func:`ensure_playback_state` it never blocks and never settles: a satellite
    that is already paused, stopped, or unreachable is left untouched, and a
    single ``pl_pause`` is sent only to one observed ``playing``.

    The "playing"-only gate is the whole safety of it.  ``pl_pause`` toggles, so
    firing it at a *stopped* VLC would START item 1 (the phantom-load trap), and
    firing a second one at an already-*paused* VLC would un-pause it.  Reading
    the state first and acting only on ``playing`` avoids both — and because
    each tick sends at most one toggle to a confirmed-playing VLC, it cannot
    oscillate the way a tight retry loop can.
    """
    if get_playback_state(port, password) == "playing":
        vlc_http_cmd(port, "pl_pause", password)
        return True
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


def _parse_playlist_entries(xml: str) -> tuple[list[tuple[int, str]], int]:
    """Return ([(id, windows_path)...], current_id) from playlist_jstree.xml.

    Returns ([], -1) if the playlist cannot be parsed.

    VLC 3.x uses ``<item id="plid_N" uri="...">`` for media items.
    Container nodes (root node, "Playlist" folder) have no ``uri=`` attribute
    and must be excluded from the navigation sequence.  The numeric suffix N
    is what VLC's ``pl_play&id=N`` command expects.
    """
    entries: list[tuple[int, str]] = []
    current_id = -1
    for attrs in re.findall(r'<item\b([^>]*)>', xml):
        uri_m = re.search(r'\buri="([^"]+)"', attrs)
        if not uri_m:
            continue  # skip container nodes (no uri= means not a media item)
        id_m = re.search(r'\bid="plid_(\d+)"', attrs)
        if not id_m:
            continue
        item_id = int(id_m.group(1))
        entries.append((item_id, decode_file_uri(uri_m.group(1))))
        if 'current="current"' in attrs:
            current_id = item_id
    return entries, current_id


def _parse_playlist_ids(xml: str) -> tuple[list[int], int]:
    entries, current_id = _parse_playlist_entries(xml)
    return [item_id for item_id, _path in entries], current_id


def get_playlist_entries(port: int, password: str) -> tuple[list[tuple[int, str]], int]:
    """The live playlist as [(plid, windows_path)...] plus the current plid."""
    status, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", password)
    if status != 200 or not xml:
        return [], -1
    return _parse_playlist_entries(xml)


def vlc_play_playlist_item(
    port: int,
    password: str,
    target_id: int,
    *,
    sleep_fn=time.sleep,
    timeout_s: float = 2.0,
) -> bool:
    """Play a playlist item by id, confirming the current item changed.

    pl_play&id can be silently ignored by VLC when jstree IDs are stale
    (e.g. after a playlist wrap), so success means an observed change.
    """
    current_path = get_current_file_path(port, password)
    if not vlc_http_cmd(port, f"pl_play&id={target_id}", password):
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if get_current_file_path(port, password) != current_path:
            return True
        sleep_fn(0.05)
    return False


def vlc_nav_step(port: int, password: str, direction: str) -> bool:
    """Navigate to the previous or next playlist item via pl_play&id=N.

    Unlike pl_previous/pl_next, this bypasses VLC's restart-threshold
    behavior (where pl_previous restarts the current track if you are
    more than a few seconds in).  The target item is resolved from the
    live playlist and played directly by ID.

    Returns True only if the item actually changed; retries once with
    fresh IDs if the first attempt produces no change within 2 seconds.

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
        if vlc_play_playlist_item(port, password, target_id):
            return True
        logger.warning(
            "vlc_nav_step: no item change after pl_play&id=%d (attempt %d), %s",
            target_id, attempt, "retrying with fresh jstree" if attempt == 0 else "giving up",
        )
        time.sleep(0.1)
    return False


def vlc_swap_current_with(
    port: int,
    password: str,
    new_path: str,
    *,
    sleep_fn=time.sleep,
) -> bool:
    """Replace the currently-playing item with *new_path* (a file not in the
    playlist): enqueue it, play it by id, then delete the old entry.

    VLC's HTTP interface has no insert-at-position, so the newcomer lands at
    the end of the playlist — acceptable for a shuffled repeat-all loop.  The
    old entry is only deleted after the newcomer demonstrably started, so a
    failed swap leaves the playlist intact.
    """
    entries, current_id = get_playlist_entries(port, password)
    if not entries or current_id < 0:
        logger.warning("vlc_swap_current_with: could not resolve playlist position")
        return False
    if not send_vlc_input_command(port, "in_enqueue", new_path, password):
        return False
    sleep_fn(0.1)
    known_ids = {item_id for item_id, _path in entries}
    new_entries, _ = get_playlist_entries(port, password)
    new_key = normalize_path_key(new_path)
    added = [
        item_id for item_id, path in new_entries
        if item_id not in known_ids and normalize_path_key(path) == new_key
    ]
    if not added:
        logger.warning("vlc_swap_current_with: enqueued item did not appear: %s", new_path)
        return False
    if not vlc_play_playlist_item(port, password, added[0], sleep_fn=sleep_fn):
        return False
    vlc_http_cmd(port, f"pl_delete&id={current_id}", password)
    return True


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


def retarget_playlist_keeping_current(
    port: int,
    password: str,
    desired_paths: list[str],
    *,
    repeat_mode: str = "all",
    sleep_fn=time.sleep,
) -> bool:
    """Reshape the live playlist to hold *desired_paths*, without disturbing the
    clip currently playing.

    A playlist *replace* (``pl_empty`` + ``in_play``) restarts on item 0 — for a
    loop toggle that yanks you off the video on screen.  To change only what a
    satellite plays *next*, the queue is edited in place around the current item:

    * each desired path not already queued is enqueued (``in_enqueue``, landing
      at the end) — done first, so the current clip always has somewhere to go;
    * each queued item that is not desired, except the one playing, is deleted
      (``pl_delete``);
    * ``repeat_mode`` is applied last so the reshaped queue cycles.

    The playing item is never re-enqueued nor deleted, so playback continues
    uninterrupted; when the clip ends VLC advances into the new queue.  Returns
    False if the playlist could not be read (nothing is changed then) or the
    repeat mode never took.
    """
    entries, current_id = get_playlist_entries(port, password)
    if current_id < 0:
        return False
    present_keys = {normalize_path_key(path) for _item_id, path in entries}
    desired_keys = {normalize_path_key(path) for path in desired_paths}
    for path in desired_paths:
        key = normalize_path_key(path)
        if key not in present_keys:
            send_vlc_input_command(port, "in_enqueue", path, password)
            present_keys.add(key)  # don't enqueue the same clip twice in one call
    for item_id, path in entries:
        if item_id != current_id and normalize_path_key(path) not in desired_keys:
            vlc_http_cmd(port, f"pl_delete&id={item_id}", password)
    if repeat_mode:
        return set_repeat_mode(port, password, repeat_mode, sleep_fn=sleep_fn)
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
