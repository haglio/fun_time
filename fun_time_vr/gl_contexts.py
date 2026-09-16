from __future__ import annotations

import glfw

WINDOW_SIZE = (320, 200)


def hidden_gl_window(title: str, *, share=None):
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 4)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 5)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
    window = glfw.create_window(*WINDOW_SIZE, title, None, share)
    if not window:
        raise RuntimeError(f"Could not open the GL context for {title}")
    return window


class SharedContexts:
    def __init__(self, scene_window) -> None:
        self._scene = scene_window

    def open(self, name: str):
        return hidden_gl_window(name, share=self._scene)

    def close(self, window) -> None:
        glfw.destroy_window(window)

    @staticmethod
    def make_current(window) -> None:
        glfw.make_context_current(window)

    @staticmethod
    def get_proc_address(name: str):
        return glfw.get_proc_address(name)
