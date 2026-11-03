from __future__ import annotations

import glfw
import numpy as np
from OpenGL import GL

from .render import RenderTarget

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


class GpuMark:
    def __init__(self) -> None:
        self._query = int(GL.glGenQueries(1)[0])

    def set(self) -> None:
        GL.glBeginQuery(GL.GL_TIME_ELAPSED, self._query)
        GL.glEndQuery(GL.GL_TIME_ELAPSED)

    @property
    def reached(self) -> bool:
        return bool(GL.glGetQueryObjectiv(self._query, GL.GL_QUERY_RESULT_AVAILABLE))

    def close(self) -> None:
        GL.glDeleteQueries(1, [self._query])


class Gpu:
    def mark(self) -> GpuMark:
        return GpuMark()

    def flush(self) -> None:
        GL.glFlush()

    def copy(self, frame, target: RenderTarget) -> None:
        GL.glCopyImageSubData(
            frame.texture, GL.GL_TEXTURE_2D, 0, 0, 0, 0,
            target.texture, GL.GL_TEXTURE_2D, 0, 0, 0, 0,
            frame.width, frame.height, 1,
        )

    def read_pixels(self, target: RenderTarget) -> np.ndarray:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target.fbo)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        pixels = GL.glReadPixels(0, 0, target.width, target.height, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        return np.frombuffer(pixels, np.uint8).reshape(target.height, target.width, 4)


class SharedContexts:
    def __init__(self, scene_window) -> None:
        self._scene = scene_window

    def open(self, name: str):
        return hidden_gl_window(name, share=self._scene)

    def close(self, window) -> None:
        glfw.destroy_window(window)

    @staticmethod
    def gpu() -> Gpu:
        return Gpu()

    @staticmethod
    def render_target() -> RenderTarget:
        return RenderTarget()

    @staticmethod
    def make_current(window) -> None:
        glfw.make_context_current(window)

    @staticmethod
    def get_proc_address(name: str):
        return glfw.get_proc_address(name)
