"""Open and close the players' engine with faulthandler armed, as a host arms it.

A child process, not a test body, because what it is watching for is a crash: a
structured exception raised inside libmpv is answered by faulthandler's vectored
handler, which dumps every thread's Python frames without the GIL, and in the
wrong microsecond that walk reads a thread state python-mpv is freeing and
faults.  Nothing catches that one, so the process dies where it stands -- which
in-process would be the whole suite.

Writes faulthandler's output to *log*, so the caller can read what it caught:

    python -m tests.integration.teardown_probe <log> <rounds>
"""
from __future__ import annotations

import faulthandler
import sys
from pathlib import Path

import glfw
from player_core.render_player import MpvRenderPlayer

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
            MpvRenderPlayer(glfw.get_proc_address, muted=True).close()
    finally:
        glfw.destroy_window(window)
        glfw.terminate()
    print(f"{rounds} opened and closed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
