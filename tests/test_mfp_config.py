from __future__ import annotations

import logging
from pathlib import Path

import json

from fun_time.mfp_config import (
    _read_mfp_config_payload,
    ensure_mfp_vlc_endpoint,
    read_mfp_vlc_endpoint,
    write_mfp_vlc_endpoint,
)
from fun_time.config import load_config


class TestEnsureMfpVlcEndpoint:
    def test_reads_existing_vlc_endpoint(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config_path = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config_path.write_text(
            json.dumps(
                {
                    "MediaSource": {
                        "VLC": {
                            "Endpoint": "127.0.0.1:8080",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        assert read_mfp_vlc_endpoint(config) == "127.0.0.1:8080"

    def test_updates_mfp_vlc_endpoint_to_primary_port(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.mfp_config")
        mfp_config_path = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config_path.write_text(
            json.dumps(
                {
                    "MediaSource": {
                        "ActiveItem": "Web",
                        "VLC": {
                            "AutoConnectEnabled": False,
                            "Endpoint": "127.0.0.1:8080",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        result = ensure_mfp_vlc_endpoint(config, logger)

        assert result == "127.0.0.1:8090"
        payload = json.loads(mfp_config_path.read_text(encoding="utf-8"))
        assert payload["MediaSource"]["ActiveItem"] == "VLC"
        assert payload["MediaSource"]["VLC"]["AutoConnectEnabled"] is True
        assert payload["MediaSource"]["VLC"]["Endpoint"] == "127.0.0.1:8090"


class TestEnsureMfpVlcEndpointEdgeCases:
    def test_returns_current_when_already_matches(self, cfg_path: Path):
        config = load_config(cfg_path)
        logger = logging.getLogger("test.mfp_config")
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({
            "MediaSource": {"VLC": {"Endpoint": f"127.0.0.1:{config.vlc.primary_vlc_http_port}"}}
        }), encoding="utf-8")
        result = ensure_mfp_vlc_endpoint(config, logger)
        assert result == f"127.0.0.1:{config.vlc.primary_vlc_http_port}"


class TestMfpConfigEdgeCases:
    def test_read_mfp_config_returns_empty_when_file_missing(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        if mfp_config.exists():
            mfp_config.unlink()
        assert _read_mfp_config_payload(config) == {}

    def test_read_mfp_config_returns_empty_for_invalid_json(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text("NOT JSON", encoding="utf-8")
        assert _read_mfp_config_payload(config) == {}

    def test_read_mfp_vlc_endpoint_returns_none_for_missing_keys(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": "not_a_dict"}), encoding="utf-8")
        assert read_mfp_vlc_endpoint(config) is None

    def test_read_mfp_vlc_endpoint_returns_none_when_vlc_not_dict(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": {"VLC": "not_a_dict"}}), encoding="utf-8")
        assert read_mfp_vlc_endpoint(config) is None

    def test_read_mfp_vlc_endpoint_returns_none_when_endpoint_not_string(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": {"VLC": {"Endpoint": 12345}}}), encoding="utf-8")
        assert read_mfp_vlc_endpoint(config) is None

    def test_write_mfp_vlc_endpoint_repairs_malformed_media_source(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": "broken"}), encoding="utf-8")
        write_mfp_vlc_endpoint(config, "127.0.0.1:8080")
        payload = json.loads(mfp_config.read_text(encoding="utf-8"))
        assert payload["MediaSource"]["VLC"]["Endpoint"] == "127.0.0.1:8080"

    def test_write_mfp_vlc_endpoint_repairs_malformed_vlc_source(self, cfg_path: Path):
        config = load_config(cfg_path)
        mfp_config = config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")
        mfp_config.write_text(json.dumps({"MediaSource": {"VLC": "broken"}}), encoding="utf-8")
        write_mfp_vlc_endpoint(config, "127.0.0.1:8080")
        payload = json.loads(mfp_config.read_text(encoding="utf-8"))
        assert payload["MediaSource"]["VLC"]["Endpoint"] == "127.0.0.1:8080"
