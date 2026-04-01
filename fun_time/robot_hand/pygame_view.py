from __future__ import annotations

import os

import numpy as np
import pygame
from pygame._sdl2.video import Renderer, Texture, Window


class PygameView:
    def __init__(
        self,
        *,
        width: int,
        height: int,
        x: int = 0,
        y: int = 0,
        title: str = "Robot Hand",
    ) -> None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"
        pygame.init()
        self.window = Window(title, size=(width, height))
        self.renderer = Renderer(self.window, accelerated=True)
        self.clock = pygame.time.Clock()
        self._width = width
        self._height = height
        self._current_texture: Texture | None = None

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def get_size(self) -> tuple[int, int]:
        return self.window.size

    def display_frame(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        surface = pygame.image.frombuffer(frame.tobytes(), (w, h), "RGB")
        if self._current_texture is not None:
            self._current_texture.destroy()
        self._current_texture = Texture.from_surface(self.renderer, surface)
        self.renderer.clear()
        self._current_texture.draw()
        self.renderer.present()

    def show(self) -> None:
        self.window.show()

    def hide(self) -> None:
        self.window.hide()

    def set_title(self, title: str) -> None:
        self.window.title = title

    def destroy(self) -> None:
        if self._current_texture is not None:
            self._current_texture.destroy()
            self._current_texture = None
        self.renderer.destroy()
        self.window.destroy()
        pygame.quit()
