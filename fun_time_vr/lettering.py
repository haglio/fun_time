from __future__ import annotations

from functools import cache

from PIL import ImageFont

# Segoe UI by file, the way Pillow loads a face: "b" is its bold, "z" its bold
# italic -- the lean the app's name wears everywhere else it is written.
BOLD_FACE = "segoeuib.ttf"
REGULAR_FACE = "segoeui.ttf"
WORDMARK_FACE = "segoeuiz.ttf"


@cache
def load_font(px: int, face: str = BOLD_FACE) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(face, px)
    except OSError:
        return ImageFont.load_default(px)


def fit_text(font, text: str, width: int) -> str:
    if font.getlength(text) <= width or not text:
        return text
    kept = text
    while kept and font.getlength(kept + "…") > width:
        kept = kept[:-1]
    return kept + "…"


def wrap_text(font, text: str, width: int, *, max_lines: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and font.getlength(f"{lines[-1]} {word}") <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    if len(lines) > max_lines:
        lines[max_lines - 1:] = [" ".join(lines[max_lines - 1:])]
    return [fit_text(font, line, width) for line in lines]
