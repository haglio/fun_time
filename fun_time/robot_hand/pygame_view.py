from __future__ import annotations

from pathlib import Path

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
        icon_path: Path | None = None,
    ) -> None:
        pygame.init()
        self.window = Window(title, size=(width, height))
        self.window.position = (x, y)
        if icon_path is not None and icon_path.exists():
            try:
                icon_surface = pygame.image.load(str(icon_path))
                self.window.set_icon(icon_surface)
            except Exception:
                pass
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
        self._current_texture = None
        self.window.destroy()
        pygame.quit()
