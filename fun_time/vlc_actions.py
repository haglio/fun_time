from __future__ import annotations

import base64
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


def vlc_http_req(port: int, path: str, password: str, user: str = "") -> tuple[int, str]:
    url = f"http://127.0.0.1:{port}{path}"
    credentials = f"{user}:{password}".encode("utf-8")
    auth = "Basic " + base64.b64encode(credentials).decode("ascii")
    request = urllib.request.Request(url, headers={"Authorization": auth})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = getattr(response, "status", 200)
            return status, response.read().decode("utf-8", errors="replace")
    except Exception:
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
