from __future__ import annotations

import faulthandler
import sys
from pathlib import Path

import glfw
from funestra.render_engine import MpvRenderEngine

from fun_time_vr.gl_contexts import hidden_gl_window


def main() -> int:
    log_path, rounds = Path(sys.argv[1]), int(sys.argv[2])
    faulthandler.enable(log_path.open("w", encoding="utf-8", buffering=1), all_threads=True)

    if not glfw.init():
        raise RuntimeError("glfw failed to initialize")
    window = hidden_gl_window("teardown-probe")
    glfw.make_context_current(window)
    try:
        for _ in range(rounds):
            MpvRenderEngine(glfw.get_proc_address, muted=True).close()
    finally:
        glfw.destroy_window(window)
        glfw.terminate()
    print(f"{rounds} opened and closed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
