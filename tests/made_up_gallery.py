from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fun_time.gallery import CONTRACT_FILE

DATABASE = "state/origenerator.db"
COLUMNS = ("prompt_id", "output_files", "positive_prompt", "negative_prompt", "seed", "params_json",
           "source", "starred", "evolver_upscale", "web_url", "watch_completions", "watch_skips",
           "watch_locks", "recipe_category", "combined_labels", "shape", "video_seconds",
           "genau_flipped")


def made_up_origenerator(root: Path, *rows: dict) -> Path:
    checkout = root / "made up origenerator"
    (checkout / DATABASE).parent.mkdir(parents=True, exist_ok=True)
    (checkout / CONTRACT_FILE).write_text(json.dumps({
        "database_path": DATABASE,
        "records_command": [".venv/Scripts/python.exe", "-m", "origenerator.ai_video_records"],
    }), encoding="utf-8")
    connection = sqlite3.connect(checkout / DATABASE)
    connection.execute(f"CREATE TABLE generations ({', '.join(COLUMNS)})")
    connection.executemany(
        f"INSERT INTO generations VALUES ({', '.join('?' * len(COLUMNS))})",
        [tuple(row.get(column) for column in COLUMNS) for row in rows])
    connection.commit()
    connection.close()
    return checkout


def made_up_gallery(root: Path, *rows: dict) -> Path:
    return made_up_origenerator(root, *rows) / DATABASE


def a_row(prompt_id: str, name: str, *, params: dict | None = None, **columns) -> dict:
    return {"prompt_id": prompt_id,
            "output_files": json.dumps([{"filename": name, "subfolder": "video"}]),
            "params_json": json.dumps(params or {}), "source": "outside", **columns}


def a_video_from_a_start_image(name: str, frame: str, *, video: dict, image: dict,
                               **columns) -> tuple[dict, dict]:
    return (a_row(f"gen {name}", name, params={**video, "input_image": frame}, **columns),
            {"prompt_id": f"gen {frame}", "source": "outside",
             "output_files": json.dumps([{"filename": frame, "subfolder": "image"}]),
             "params_json": json.dumps(image)})
