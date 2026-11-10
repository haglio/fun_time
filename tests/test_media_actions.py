from __future__ import annotations

from fun_time.content import WebProvider
from fun_time.media_actions import make_web_url_from_path

_PROVIDERS_NO_PRIVATE_OVERLAY_SUPPLIES = (
    WebProvider(marker="alpha", gallery_url="https://example.com/alpha/{id}"),
    WebProvider(marker="beta", gallery_url="https://example.com/beta/{id}"),
)


def test_make_web_url_from_path_supports_known_roots():
    assert make_web_url_from_path(r"C:\images\alpha\abc_123.png", _PROVIDERS_NO_PRIVATE_OVERLAY_SUPPLIES) == "https://example.com/alpha/abc"
    assert make_web_url_from_path(r"C:\images\beta\def_456.jpg", _PROVIDERS_NO_PRIVATE_OVERLAY_SUPPLIES) == "https://example.com/beta/def"
    assert make_web_url_from_path(r"C:\images\other\ghi_789.jpg", _PROVIDERS_NO_PRIVATE_OVERLAY_SUPPLIES) == ""
