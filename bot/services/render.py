"""Рендер текстового ответа в PNG-картинку (опция «ответ картинкой»).

Светлый фон, шрифт DejaVu, перенос строк по словам. Моноширинный DejaVu Sans Mono
используется внутри блоков кода ```...```.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Параметры картинки
PADDING = 40          # отступы по краям
LINE_SPACING = 10     # дополнительный интервал между строками
FONT_SIZE = 22        # размер обычного шрифта
CODE_FONT_SIZE = 20   # размер моноширинного шрифта для кода
MAX_WIDTH = 1080      # ширина картинки, px

# Цвета (светлая тема)
BG_COLOR = (252, 252, 250)
TEXT_COLOR = (32, 32, 32)
CODE_BG_COLOR = (240, 240, 236)
CODE_TEXT_COLOR = (40, 44, 52)

# Кандидаты путей к шрифтам DejaVu: Docker (fonts-dejavu), macOS, Ubuntu/Debian
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/DejaVuSans.ttf",
]
_MONO_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Supplemental/DejaVuSansMono.ttf",
]


def _find_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    """Загрузить первый найденный шрифт из списка, иначе встроенный растровый."""
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    logger.warning("Шрифт DejaVu не найден, используется встроенный шрифт Pillow")
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Перенос строки по словам: разбиваем текст на строки заданной ширины."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}".strip() if current else word
            if font.getlength(candidate) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                # Слишком длинное слово (например, URL) — режем посимвольно
                while word and font.getlength(word) > max_width:
                    cut = word
                    while len(cut) > 1 and font.getlength(cut) > max_width:
                        cut = cut[:-1]
                    lines.append(cut)
                    word = word[len(cut):]
                current = word
        lines.append(current)
    return lines


class _Segment:
    """Строка текста с информацией о том, рисовать ли её как код."""

    __slots__ = ("text", "is_code")

    def __init__(self, text: str, is_code: bool) -> None:
        self.text = text
        self.is_code = is_code


def _build_segments(text: str) -> list[_Segment]:
    """Разбить текст на обычные строки и строки внутри блоков кода ```...```."""
    segments: list[_Segment] = []
    parts = text.split("```")
    font = _find_font(_FONT_CANDIDATES, FONT_SIZE)
    code_font = _find_font(_MONO_CANDIDATES, CODE_FONT_SIZE)
    text_width = MAX_WIDTH - 2 * PADDING

    for i, part in enumerate(parts):
        is_code = i % 2 == 1
        # Убираем имя языка после открывающих кавычек блока кода
        if is_code:
            part = re.sub(r"^[a-zA-Z0-9_+-]*\n", "", part, count=1)
        wrapped = _wrap_text(part.strip("\n"), code_font if is_code else font, text_width - (16 if is_code else 0))
        for line in wrapped:
            segments.append(_Segment(line, is_code))
    return segments


def render_answer_png(text: str) -> bytes:
    """Отрендерить текст ответа в PNG и вернуть байты изображения."""
    segments = _build_segments(text)

    font = _find_font(_FONT_CANDIDATES, FONT_SIZE)
    code_font = _find_font(_MONO_CANDIDATES, CODE_FONT_SIZE)

    # Считаем высоту: обычный текст и код имеют разный размер шрифта
    line_height = FONT_SIZE + LINE_SPACING
    code_line_height = CODE_FONT_SIZE + LINE_SPACING
    code_block_padding = 16  # внутренние отступы блока кода

    total_height = PADDING * 2
    prev_is_code = False
    for seg in segments:
        if seg.is_code and not prev_is_code:
            total_height += code_block_padding  # верхний отступ блока кода
        elif prev_is_code and not seg.is_code:
            total_height += code_block_padding  # нижний отступ блока кода
        total_height += code_line_height if seg.is_code else line_height
        prev_is_code = seg.is_code
    if prev_is_code:
        total_height += code_block_padding

    image = Image.new("RGB", (MAX_WIDTH, max(total_height, 200)), BG_COLOR)
    draw = ImageDraw.Draw(image)

    y = PADDING
    prev_is_code = False
    for seg in segments:
        if seg.is_code and not prev_is_code:
            y += code_block_padding
        elif prev_is_code and not seg.is_code:
            y += code_block_padding

        if seg.is_code:
            # Подложка под строку кода
            draw.rectangle(
                [PADDING - 8, y - 2, MAX_WIDTH - PADDING + 8, y + CODE_FONT_SIZE + 4],
                fill=CODE_BG_COLOR,
            )
            draw.text((PADDING + 8, y), seg.text, font=code_font, fill=CODE_TEXT_COLOR)
            y += code_line_height
        else:
            draw.text((PADDING, y), seg.text, font=font, fill=TEXT_COLOR)
            y += line_height
        prev_is_code = seg.is_code

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
