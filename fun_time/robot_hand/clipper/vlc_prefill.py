from __future__ import annotations

import base64
import ctypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ...config import load_config
from .paths import MODULE_DIR
from .utils import format_seconds, sanitize_name

_VLC_TITLE_SUFFIXES = (
    " - VLC media player",
    " - VLC media player (Direct3D11 output)",
)
_VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v")
_TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\b")


@dataclass(frozen=True)
class VlcSessionPrefill:
    video_file: str
    session_name: str
    timestamp: str
    note: str


@dataclass(frozen=True)
class _VlcProbe:
    media_path: Path
    position_seconds: float | None = None


def detect_vlc_session_prefill() -> VlcSessionPrefill | None:
    probe = _detect_from_http()
    if probe is None:
        probe = _detect_from_windows()
    if probe is None:
        return None
    timestamp = format_seconds(probe.position_seconds or 0.0)
    note = (
        f"Prefilled from VLC: {probe.media_path.name} at {timestamp}."
        if probe.position_seconds is not None
        else f"Prefilled from VLC: {probe.media_path.name}. Timestamp defaulted to {timestamp}."
    )
    return VlcSessionPrefill(
        video_file=str(probe.media_path),
        session_name=sanitize_name(probe.media_path.stem),
        timestamp=timestamp,
        note=note,
    )


def _detect_from_http() -> _VlcProbe | None:
    for port in _candidate_http_ports():
        payload = _fetch_http_status(port)
        if payload is None:
            continue
        media_path = _media_path_from_http_payload(payload)
        if media_path is None:
            continue
        return _VlcProbe(media_path=media_path, position_seconds=_http_time_seconds(payload))
    return None


def _detect_from_windows() -> _VlcProbe | None:
    for title in _ordered_vlc_window_titles():
        media_path = _resolve_media_path_from_title(title)
        if media_path is None:
            continue
        return _VlcProbe(media_path=media_path, position_seconds=_timestamp_seconds_from_title(title))
    return None


def _candidate_http_ports() -> list[int]:
    ports = [8080]
    try:
        config = load_config()
    except Exception:
        return ports
    for port in (config.controller.vlc2_http_port, config.controller.vlc3_http_port):
        if port not in ports:
            ports.append(port)
    return ports


def _fetch_http_status(port: int) -> ET.Element | None:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/requests/status.xml")
    password = _vlc_http_password()
    if password:
        token = base64.b64encode(f":{password}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=0.2) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        return ET.fromstring(payload)
    except ET.ParseError:
        return None


def _media_path_from_http_payload(payload: ET.Element) -> Path | None:
    info = payload.find("./information/category[@name='meta']")
    if info is None:
        return None
    raw = (
        _text_of(info.find("./info[@name='url']"))
        or _text_of(info.find("./info[@name='filename']"))
        or _text_of(info.find("./info[@name='title']"))
    )
    return _resolve_media_path(raw)


def _http_time_seconds(payload: ET.Element) -> float | None:
    raw = _text_of(payload.find("./time"))
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _ordered_vlc_window_titles() -> list[str]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    titles: list[str] = []
    seen: set[str] = set()
    foreground = user32.GetForegroundWindow()

    def append_title(hwnd: int) -> None:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not _looks_like_vlc_title(title) or title in seen:
            return
        seen.add(title)
        titles.append(title)

    if foreground:
        append_title(foreground)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        append_title(hwnd)
        return True

    user32.EnumWindows(enum_proc, 0)
    return titles


def _looks_like_vlc_title(title: str) -> bool:
    lower = title.lower()
    return any(suffix.lower() in lower for suffix in _VLC_TITLE_SUFFIXES)


def _timestamp_seconds_from_title(title: str) -> float | None:
    match = _TIMESTAMP_RE.search(title)
    if match is None:
        return None
    parts = match.group(0).split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def _resolve_media_path_from_title(title: str) -> Path | None:
    cleaned = _strip_vlc_title_suffix(title)
    if not cleaned:
        return None
    cleaned = _TIMESTAMP_RE.sub("", cleaned).strip(" -\u2013")
    return _resolve_media_path(cleaned)


def _resolve_media_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    candidate = raw.strip().strip('"')
    if not candidate:
        return None
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme == "file":
        candidate = urllib.request.url2pathname(parsed.path)
    path = Path(candidate)
    if path.is_file():
        return path
    filename = path.name or candidate
    if not filename:
        return None
    return _search_roots_for_filename(filename)


def _strip_vlc_title_suffix(title: str) -> str:
    result = title.strip()
    for suffix in _VLC_TITLE_SUFFIXES:
        if result.endswith(suffix):
            return result[: -len(suffix)].strip()
    return result


@lru_cache(maxsize=1)
def _search_roots() -> tuple[Path, ...]:
    roots: list[Path] = [MODULE_DIR / "raw_clips"]
    try:
        config = load_config()
    except Exception:
        config = None
    if config is not None:
        roots.extend(config.paths.primary_vlc_dirs)
        roots.extend(config.paths.portrait_dirs)
        roots.extend(config.paths.landscape_dirs)
        roots.append(config.paths.weird_dir)
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return tuple(deduped)


def _search_roots_for_filename(filename: str) -> Path | None:
    candidates = [filename]
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    if not suffix:
        candidates.extend(f"{stem}{ext}" for ext in _VIDEO_EXTENSIONS)
    for root in _search_roots():
        for name in candidates:
            direct = root / name
            if direct.is_file():
                return direct
        for name in candidates:
            match = next(root.rglob(name), None)
            if match is not None and match.is_file():
                return match
    return None


def _text_of(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


@lru_cache(maxsize=1)
def _vlc_http_password() -> str | None:
    return (
        os.environ.get("FUN_TIME_VLC_HTTP_PASS")
        or os.environ.get("VLC_HTTP_PASSWORD")
        or _vlc_http_password_from_config()
    )


def _vlc_http_password_from_config() -> str | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    vlcrc = Path(appdata) / "vlc" / "vlcrc"
    try:
        with vlcrc.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("http-password="):
                    value = stripped.split("=", 1)[1].strip()
                    return value or None
    except OSError:
        return None
    return None
