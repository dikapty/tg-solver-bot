"""Подготовка изображений: сжатие перед отправкой в Anthropic API.

Правила API по изображениям: длинная сторона не более 1568 px (иначе сервер
её уменьшит сам, но лучше сделать это заранее и не платить за лишние байты),
общий размер запроса не более 32 МБ, поддерживаются JPEG, PNG, WebP, GIF.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Целевой размер длинной стороны после сжатия
MAX_SIDE_PX = 1568

# Качество JPEG при перекодировании
JPEG_QUALITY = 85

# Ограничение на размер исходного файла, байт (защита от «бомб» и огромных сканов)
MAX_INPUT_BYTES = 20 * 1024 * 1024

# MIME-типы, которые понимает API
SUPPORTED_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})


@dataclass(frozen=True)
class PreparedImage:
    """Изображение, готовое к отправке в модель."""

    media_type: str
    data: bytes


class ImageTooLargeError(Exception):
    """Файл больше допустимого размера."""


class UnsupportedImageError(Exception):
    """Файл не является поддерживаемым изображением."""


def prepare_image(raw: bytes, original_mime: str | None = None) -> PreparedImage:
    """Сжать изображение до ~1568px по длинной стороне и перекодировать в JPEG.

    Анимированные GIF не перекодируются (API принимает только первый кадр,
    но GIF отдаём как есть, если он проходит по размеру).
    """
    if len(raw) > MAX_INPUT_BYTES:
        raise ImageTooLargeError(
            f"Файл слишком большой ({len(raw) / 1024 / 1024:.1f} МБ, максимум {MAX_INPUT_BYTES // 1024 // 1024} МБ)."
        )

    if not raw:
        raise UnsupportedImageError("Получен пустой файл.")

    # GIF отдаём без перекодирования, если он поддерживается API
    if (original_mime or "").lower() == "image/gif":
        return PreparedImage(media_type="image/gif", data=raw)

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.load()

            # Приводим к RGB: палитры и альфа-канал в JPEG не переносятся
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            long_side = max(img.size)
            if long_side > MAX_SIDE_PX:
                scale = MAX_SIDE_PX / long_side
                new_size = (
                    max(1, round(img.width * scale)),
                    max(1, round(img.height * scale)),
                )
                img = img.resize(new_size, Image.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            data = buffer.getvalue()
    except UnidentifiedImageError as exc:
        raise UnsupportedImageError("Файл не является изображением.") from exc
    except (OSError, ValueError) as exc:
        raise UnsupportedImageError(f"Не удалось обработать изображение: {exc}") from exc

    logger.info(
        "Изображение подготовлено: %d -> %d байт", len(raw), len(data)
    )
    return PreparedImage(media_type="image/jpeg", data=data)


def is_supported_mime(mime: str | None) -> bool:
    """Проверить MIME-тип файла, присланного как документ."""
    return (mime or "").lower() in SUPPORTED_MEDIA_TYPES
