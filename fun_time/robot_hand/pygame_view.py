from __future__ import annotations

from pathlib import Path

import numpy as np
import pygame
from PIL import Image
from pygame._sdl2.video import Renderer, Texture, Window


def _get_window_chrome_height() -> int:
    try:
        import ctypes
        SM_CYCAPTION = 4
        SM_CYFRAME = 33
        SM_CXPADDEDBORDER = 92
        user32 = ctypes.windll.user32
        return (
            user32.GetSystemMetrics(SM_CYCAPTION)
            + user32.GetSystemMetrics(SM_CYFRAME)
            + user32.GetSystemMetrics(SM_CXPADDEDBORDER)
        )
    except Exception:
        return 0


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
        chrome_height = _get_window_chrome_height()
        client_height = max(1, height - chrome_height)
        self.window = Window(title, size=(width, client_height))
        self.window.position = (x, y + chrome_height)
        if icon_path is not None and icon_path.exists():
            try:
                pil_icon = Image.open(str(icon_path))
                pil_icon = pil_icon.convert("RGBA")
                icon_surface = pygame.image.frombuffer(
                    pil_icon.tobytes(), pil_icon.size, "RGBA"
                )
                self.window.set_icon(icon_surface)
            except Exception:
                pass
        self.renderer = Renderer(self.window, accelerated=True)
        self.clock = pygame.time.Clock()
        self._width = width
        self._height = height
        self._current_texture: Texture | None = None
        self._loading_font: pygame.font.Font | None = None
        self._loading_text: str | None = None

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def get_size(self) -> tuple[int, int]:
        return self.window.size

    def set_loading_text(self, text: str | None) -> None:
        self._loading_text = text

    def display_frame(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        surface = pygame.image.frombuffer(frame.tobytes(), (w, h), "RGB")
        self._current_texture = Texture.from_surface(self.renderer, surface)
        self.renderer.clear()
        self._current_texture.draw()
        if self._loading_text:
            self._draw_loading_overlay()
        self.renderer.present()

    def _draw_loading_overlay(self) -> None:
        if self._loading_font is None:
            self._loading_font = pygame.font.SysFont("arial", 18)
        text_surface = self._loading_font.render(self._loading_text, True, (255, 255, 255))
        padding = 8
        w, h = text_surface.get_size()
        bg = pygame.Surface((w + padding * 2, h + padding * 2), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 180))
        bg.blit(text_surface, (padding, padding))
        texture = Texture.from_surface(self.renderer, bg)
        win_w, _win_h = self.window.size
        dest = pygame.Rect(win_w - w - padding * 3, padding, w + padding * 2, h + padding * 2)
        texture.draw(dstrect=dest)

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
