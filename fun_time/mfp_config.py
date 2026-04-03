from __future__ import annotations

import json
import re
from pathlib import Path


def mfp_config_path(config) -> Path:
    return config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")


def _read_mfp_config_payload(config) -> dict:
    config_path = mfp_config_path(config)
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_mfp_config_payload(config, payload: dict) -> None:
    config_path = mfp_config_path(config)
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_mfp_vlc_endpoint(config) -> str | None:
    payload = _read_mfp_config_payload(config)
    media_source = payload.get("MediaSource")
    if not isinstance(media_source, dict):
        return None
    vlc_source = media_source.get("VLC")
    if not isinstance(vlc_source, dict):
        return None
    endpoint = vlc_source.get("Endpoint")
    if not isinstance(endpoint, str):
        return None
    return endpoint


def write_mfp_vlc_endpoint(config, endpoint: str) -> None:
    payload = _read_mfp_config_payload(config)
    media_source = payload.setdefault("MediaSource", {})
    if not isinstance(media_source, dict):
        media_source = {}
        payload["MediaSource"] = media_source
    media_source["ActiveItem"] = "VLC"
    vlc_source = media_source.setdefault("VLC", {})
    if not isinstance(vlc_source, dict):
        vlc_source = {}
        media_source["VLC"] = vlc_source
    vlc_source["AutoConnectEnabled"] = True
    vlc_source["Endpoint"] = endpoint
    _write_mfp_config_payload(config, payload)


def ensure_mfp_vlc_endpoint(config, logger) -> str:
    desired = f"127.0.0.1:{config.vlc.primary_vlc_http_port}"
    current = read_mfp_vlc_endpoint(config)
    if current == desired:
        return current
    write_mfp_vlc_endpoint(config, desired)
    logger.info("Updated MFP VLC endpoint to %s", desired)
    return desired
