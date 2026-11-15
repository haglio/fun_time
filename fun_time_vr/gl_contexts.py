from __future__ import annotations

import glfw
import numpy as np
from OpenGL import GL, WGL

from fun_time import preview_marker
from fun_time.project_paths import PROJECT_VR_ICON
from fun_time.win32 import draw_nothing_at_all, minimize_window, set_the_icon_alt_tab_draws

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
        self._query_standing_in_for_a_fence = int(GL.glGenQueries(1)[0])

    def set(self) -> None:
        GL.glBeginQuery(GL.GL_TIME_ELAPSED, self._query_standing_in_for_a_fence)
        GL.glEndQuery(GL.GL_TIME_ELAPSED)

    @property
    def reached(self) -> bool:
        return bool(GL.glGetQueryObjectiv(self._query_standing_in_for_a_fence, GL.GL_QUERY_RESULT_AVAILABLE))

    def close(self) -> None:
        GL.glDeleteQueries(1, [self._query_standing_in_for_a_fence])


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


class SceneWindow:
    def __init__(self) -> None:
        self._window = None
        self._fbo = 0

    def open(self, title: str) -> None:
        if not glfw.init():
            raise RuntimeError("Failed to initialize GLFW")
        try:
            self._window = hidden_gl_window(title)
        except RuntimeError:
            glfw.terminate()
            raise
        glfw.make_context_current(self._window)
        hwnd = glfw.get_win32_window(self._window)
        self._wear_the_sessions_taskbar_button(hwnd)
        draw_nothing_at_all(hwnd)
        minimize_window(hwnd, activate_the_next_window=False)

    @staticmethod
    def _wear_the_sessions_taskbar_button(hwnd: int) -> None:
        set_the_icon_alt_tab_draws(hwnd, preview_marker.icon_file(PROJECT_VR_ICON, preview_marker.shown_as()))
        preview_marker.dress_the_window(hwnd)

    @staticmethod
    def drawing_context() -> tuple[int, int]:
        return WGL.wglGetCurrentDC(), WGL.wglGetCurrentContext()

    def make_framebuffer(self) -> None:
        self._fbo = GL.glGenFramebuffers(1)

    def draw_into(self, texture: int, width: int, height: int) -> None:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, texture, 0)
        GL.glViewport(0, 0, width, height)

    @staticmethod
    def done_drawing() -> None:
        GL.glFlush()
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)

    def close_requested(self) -> bool:
        return bool(glfw.window_should_close(self._window))

    def shared_contexts(self) -> SharedContexts:
        return SharedContexts(self._window)

    def close(self) -> None:
        if self._fbo:
            GL.glDeleteFramebuffers(1, [self._fbo])
        if self._window:
            glfw.destroy_window(self._window)
        glfw.terminate()
