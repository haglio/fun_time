from __future__ import annotations


def scene_starts_ms(payload: dict) -> tuple[float, ...]:
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        return ()
    starts = (scene.get("start") for scene in scenes if isinstance(scene, dict))
    return tuple(sorted({
        float(start) * 1000.0 for start in starts
        if isinstance(start, (int, float)) and not isinstance(start, bool) and start >= 0}))
