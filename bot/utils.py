"""Вспомогательные функции: настройка логирования и разбиение длинных сообщений."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

# Жёсткий лимит Telegram на длину одного текстового сообщения
TELEGRAM_MESSAGE_LIMIT = 4096

# Регулярные выражения для секретов, которые не должны попасть в логи
_BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_API_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}\b")


def _mask(value: str) -> str:
    """Заменить секретные подстроки на заглушку."""
    value = _BOT_TOKEN_RE.sub("***BOT-TOKEN***", value)
    return _API_KEY_RE.sub("***API-KEY***", value)


class SecretMaskFilter(logging.Filter):
    """Маскирует токен бота и API-ключи в тексте всех логовых записей."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_mask(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, str):
            record.args = _mask(record.args)
        return True


def setup_logging(log_file: str = "bot.log") -> None:
    """Логи в stdout и в файл с ротацией; секреты маскируются фильтром."""
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    mask = SecretMaskFilter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(mask)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(mask)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(stdout_handler)
    root.addHandler(file_handler)

    # Меньше шума: не логируем каждое событие aiogram и служебные сообщения aiosqlite
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiogram.dispatcher").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def _split_keep_code_blocks(text: str) -> list[str]:
    """Разбить текст на параграфы, не разрезая блоки кода ```...```.

    Возвращает список кусков, каждый из которых меньше лимита Telegram
    (кроме гигантских блоков кода — их режет вызывающий код).
    """
    parts = text.split("```")
    blocks: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Нечётные части — содержимое блоков кода: возвращаем обратно с ограждением
            closing = "```" if i + 1 < len(parts) else ""
            blocks.append("```" + part + closing)
        else:
            # Чётные части — обычный текст: режем по границам абзацев
            for paragraph in re.split(r"\n{2,}", part):
                paragraph = paragraph.strip("\n")
                if paragraph:
                    blocks.append(paragraph + "\n\n")
    return blocks


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Разбить длинный ответ на сообщения, помещающиеся в лимит Telegram.

    Приоритет границ: абзацы -> блоки целиком -> жёсткая резка (только если
    один блок кода или абзац длиннее лимита).
    """
    if len(text) <= limit:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.rstrip())
        current = ""

    for block in _split_keep_code_blocks(text):
        if len(block) > limit:
            # Блок сам по себе длиннее лимита — режем жёстко
            flush()
            for i in range(0, len(block), limit):
                chunks.append(block[i : i + limit])
            continue
        if len(current) + len(block) <= limit:
            current += block
        else:
            flush()
            current = block
    flush()
    return chunks
