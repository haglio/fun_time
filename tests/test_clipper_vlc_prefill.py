"""Tests for fun_time.robot_hand.clipper.vlc_prefill."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.robot_hand.clipper.vlc_prefill import (
    VlcSessionPrefill,
    _VlcProbe,
    _resolve_media_path,
    _strip_vlc_title_suffix,
    _timestamp_seconds_from_title,
    _vlc_http_password,
    _vlc_http_password_from_config,
    detect_vlc_session_prefill,
)


class TestDetectVlcSessionPrefill:
    def test_prefers_http_probe(self, tmp_path: Path):
        video = tmp_path / "alpha.mp4"
        video.write_bytes(b"")
        with (
            patch("fun_time.robot_hand.clipper.vlc_prefill._detect_from_http", return_value=_VlcProbe(video, 12.5)),
            patch("fun_time.robot_hand.clipper.vlc_prefill._detect_from_windows") as windows_probe,
        ):
            result = detect_vlc_session_prefill()

        assert result == VlcSessionPrefill(
            video_file=str(video),
            session_name="alpha",
            timestamp="00:00:12.500",
            note="Prefilled from VLC: alpha.mp4 at 00:00:12.500.",
        )
        windows_probe.assert_not_called()

    def test_defaults_timestamp_when_only_file_is_known(self, tmp_path: Path):
        video = tmp_path / "beta clip.mp4"
        video.write_bytes(b"")
        with (
            patch("fun_time.robot_hand.clipper.vlc_prefill._detect_from_http", return_value=None),
            patch("fun_time.robot_hand.clipper.vlc_prefill._detect_from_windows", return_value=_VlcProbe(video, None)),
        ):
            result = detect_vlc_session_prefill()

        assert result == VlcSessionPrefill(
            video_file=str(video),
            session_name="beta clip",
            timestamp="00:00:00.000",
            note="Prefilled from VLC: beta clip.mp4. Timestamp defaulted to 00:00:00.000.",
        )

    def test_returns_none_when_nothing_detected(self):
        with (
            patch("fun_time.robot_hand.clipper.vlc_prefill._detect_from_http", return_value=None),
            patch("fun_time.robot_hand.clipper.vlc_prefill._detect_from_windows", return_value=None),
        ):
            assert detect_vlc_session_prefill() is None


class TestResolveMediaPath:
    def test_resolves_file_uri(self, tmp_path: Path):
        video = tmp_path / "gamma.mp4"
        video.write_bytes(b"")
        uri = video.resolve().as_uri()

        result = _resolve_media_path(uri)

        assert result == video.resolve()

    def test_looks_up_filename_in_search_roots(self, tmp_path: Path):
        root = tmp_path / "videos"
        root.mkdir()
        video = root / "delta.mp4"
        video.write_bytes(b"")
        with patch("fun_time.robot_hand.clipper.vlc_prefill._search_roots", return_value=(root,)):
            result = _resolve_media_path("delta.mp4")
        assert result == video


class TestTitleParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Example.mp4 - VLC media player", "Example.mp4"),
            ("Example.mp4 - VLC media player (Direct3D11 output)", "Example.mp4"),
            ("Example.mp4", "Example.mp4"),
        ],
    )
    def test_strips_known_suffixes(self, raw: str, expected: str):
        assert _strip_vlc_title_suffix(raw) == expected

    def test_extracts_timestamp_from_title(self):
        result = _timestamp_seconds_from_title("Example.mp4 01:02:03.500 - VLC media player")
        assert result == pytest.approx(3723.5)

    def test_returns_none_when_title_has_no_timestamp(self):
        assert _timestamp_seconds_from_title("Example.mp4 - VLC media player") is None


class TestVlcHttpPassword:
    def test_prefers_environment_variable(self):
        with (
            patch.dict("os.environ", {"FUN_TIME_VLC_HTTP_PASS": "env-secret"}, clear=False),
            patch("fun_time.robot_hand.clipper.vlc_prefill._vlc_http_password_from_config", return_value="config-secret"),
        ):
            _vlc_http_password.cache_clear()
            assert _vlc_http_password() == "env-secret"
            _vlc_http_password.cache_clear()

    def test_reads_password_from_vlcrc(self, tmp_path: Path):
        appdata = tmp_path / "appdata"
        vlc_dir = appdata / "vlc"
        vlc_dir.mkdir(parents=True)
        (vlc_dir / "vlcrc").write_text("# comment\nhttp-password=from-config\n", encoding="utf-8")

        with patch.dict("os.environ", {"APPDATA": str(appdata)}, clear=False):
            assert _vlc_http_password_from_config() == "from-config"
