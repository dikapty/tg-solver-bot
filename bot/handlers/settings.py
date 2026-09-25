"""Настройки пользователя: /mode (режим ответа кнопками) и ответ картинкой."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database
from ..prompts import MODE_BRIEF, MODE_CHILD, MODE_DETAILED, MODE_TITLES

logger = logging.getLogger(__name__)

router = Router(name="settings")

# Префиксы callback_data (лимит Telegram — 64 байта на кнопку)
CB_MODE_PREFIX = "mode"
CB_IMAGE_PREFIX = "img"

SETTINGS_TEXT = (
    "⚙️ Выберите формат ответа:\n\n"
    "• Кратко — только ответ и минимум пояснений\n"
    "• Подробно — пошаговое решение (по умолчанию)\n"
    "• Как объяснить ребёнку — простыми словами и аналогиями\n\n"
    "🖼 «Картинка» — присылать ответ в виде PNG-изображения "
    "(удобно, когда текст плохо читается)."
)


def settings_keyboard(current_mode: str, reply_image: bool) -> InlineKeyboardMarkup:
    """Inline-клавиатура настроек: режимы ответа + переключатель картинки."""
    mode_rows = []
    for mode_key in (MODE_BRIEF, MODE_DETAILED, MODE_CHILD):
        mark = "✅ " if mode_key == current_mode else ""
        mode_rows.append(
            [InlineKeyboardButton(
                text=f"{mark}{MODE_TITLES[mode_key]}",
                callback_data=f"{CB_MODE_PREFIX}:{mode_key}",
            )]
        )
    image_text = "🖼 Картинка: вкл" if reply_image else "🖼 Картинка: выкл"
    mode_rows.append([InlineKeyboardButton(text=image_text, callback_data=f"{CB_IMAGE_PREFIX}:toggle")])
    return InlineKeyboardMarkup(inline_keyboard=mode_rows)


@router.message(Command("mode"))
async def cmd_mode(message: Message, db: Database) -> None:
    """Показать настройки формата ответа."""
    if message.from_user is None:
        return
    await db.ensure_user(message.from_user.id)
    mode, reply_image = await db.get_user(message.from_user.id)
    await message.answer(
        SETTINGS_TEXT,
        reply_markup=settings_keyboard(mode, reply_image),
    )


@router.callback_query(F.data.startswith(f"{CB_MODE_PREFIX}:"))
async def cb_set_mode(callback: CallbackQuery, db: Database) -> None:
    """Выбор режима ответа кнопкой."""
    if callback.data is None or callback.from_user is None:
        await callback.answer("Ошибка: неизвестный режим", show_alert=True)
        return

    mode_key = callback.data.split(":", 1)[1]
    if mode_key not in MODE_TITLES:
        await callback.answer("Ошибка: неизвестный режим", show_alert=True)
        return

    await db.set_mode(callback.from_user.id, mode_key)
    logger.info("Пользователь %d выбрал режим %s", callback.from_user.id, mode_key)

    # Перерисовываем клавиатуру сообщения, чтобы отметка ✅ переместилась
    _, reply_image = await db.get_user(callback.from_user.id)
    if callback.message is not None:
        await callback.message.edit_reply_markup(
            reply_markup=settings_keyboard(mode_key, reply_image)
        )
    await callback.answer(f"Режим «{MODE_TITLES[mode_key]}» выбран ✅")


@router.callback_query(F.data.startswith(f"{CB_IMAGE_PREFIX}:"))
async def cb_toggle_image(callback: CallbackQuery, db: Database) -> None:
    """Переключение опции «ответ картинкой»."""
    if callback.from_user is None:
        await callback.answer("Ошибка", show_alert=True)
        return

    await db.ensure_user(callback.from_user.id)
    mode, reply_image = await db.get_user(callback.from_user.id)
    await db.set_reply_image(callback.from_user.id, not reply_image)
    new_state = not reply_image
    logger.info(
        "Пользователь %d %s ответ картинкой",
        callback.from_user.id, "включил" if new_state else "выключил",
    )

    if callback.message is not None:
        await callback.message.edit_reply_markup(
            reply_markup=settings_keyboard(mode, new_state)
        )
    await callback.answer(f"Ответ картинкой: {'включён 🖼' if new_state else 'выключен'}")
