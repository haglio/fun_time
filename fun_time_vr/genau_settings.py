"""What Genau's engine is tuned with, read off Genau's own config file.

In VR the engine runs inside Fun Time's player rather than reading its config
itself, so the ``genau`` section's numbers are read here and carried to the
player in the launch manifest's ``[vr]`` section.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GenauSettings:
    # Genau's own defaults, the ones its example config ships.
    beats_per_loop: float = 1.0
    bpm_smoothing: float = 0.14
    sync_strength: float = 0.35
    clip_cache_size: int = 2
    shuffle_on_load: bool = True
    # Where the OSR2 broker publishes its beat for Genau to follow.
    udp_host: str = "127.0.0.1"
    udp_port: int = 50555

    @classmethod
    def read(cls, genau_config_path: Path | None) -> GenauSettings:
        """The ``genau`` section over the defaults, and the defaults alone for a
        config that is not there: a session never fails to start over a number."""
        if genau_config_path is None:
            return cls()
        try:
            raw = json.loads(Path(genau_config_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        section = raw.get("genau") if isinstance(raw, dict) else None
        if not isinstance(section, dict):
            return cls()
        defaults = cls()
        return cls(
            beats_per_loop=float(section.get("beats_per_loop", defaults.beats_per_loop)),
            bpm_smoothing=float(section.get("bpm_smoothing", defaults.bpm_smoothing)),
            sync_strength=float(section.get("sync_strength", defaults.sync_strength)),
            clip_cache_size=int(section.get("clip_cache_size", defaults.clip_cache_size)),
            shuffle_on_load=bool(section.get("shuffle_on_load", defaults.shuffle_on_load)),
            udp_host=str(section.get("udp_host", defaults.udp_host)),
            udp_port=int(section.get("udp_port", defaults.udp_port)),
        )

    def manifest_fields(self) -> dict[str, str]:
        return {
            "beats_per_loop": str(self.beats_per_loop),
            "bpm_smoothing": str(self.bpm_smoothing),
            "sync_strength": str(self.sync_strength),
            "clip_cache_size": str(self.clip_cache_size),
            "shuffle_on_load": "1" if self.shuffle_on_load else "0",
            "beat_udp_host": self.udp_host,
            "beat_udp_port": str(self.udp_port),
        }

    @classmethod
    def from_manifest(cls, section) -> GenauSettings:
        """The inverse of :meth:`manifest_fields`, over a section that may predate these keys."""
        defaults = cls()
        return cls(
            beats_per_loop=float(section.get("beats_per_loop", defaults.beats_per_loop)),
            bpm_smoothing=float(section.get("bpm_smoothing", defaults.bpm_smoothing)),
            sync_strength=float(section.get("sync_strength", defaults.sync_strength)),
            clip_cache_size=int(section.get("clip_cache_size", defaults.clip_cache_size)),
            shuffle_on_load=section.get("shuffle_on_load", "1").strip() == "1",
            udp_host=section.get("beat_udp_host", defaults.udp_host),
            udp_port=int(section.get("beat_udp_port", defaults.udp_port)),
        )
