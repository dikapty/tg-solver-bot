"""Главное меню: reply-кнопки постоянной клавиатуры, команда /menu
и inline-навигация под ответами бота (callback_data «menu:…»).

Роутер регистрируется РАНЬШЕ tasks.router, чтобы текстовые кнопки меню
не попали в обработчик заданий.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..db import Database
from ..keyboards import (
    BTN_CLEAR,
    BTN_HELP,
    BTN_SOLVE,
    BTN_STATS,
    BTN_SETTINGS,
    CB_MENU_PREFIX,
    MENU_BUTTONS,
    MENU_TEXT,
    SOLVE_HINT_TEXT,
    main_menu_kb,
)

logger = logging.getLogger(__name__)

router = Router(name="menu")


# ---------------------------------------------------------------- reply-меню

@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    """Команда /menu — показать главное меню и постоянную клавиатуру."""
    await message.answer(MENU_TEXT, reply_markup=main_menu_kb())


@router.message(F.text.in_(MENU_BUTTONS))
async def handle_menu_buttons(message: Message, db: Database) -> None:
    """Нажатия кнопок постоянной reply-клавиатуры.

    Настройки и помощь делегируем существующим командам: вызываем их
    хэндлеры напрямую, чтобы поведение кнопок совпадало с /mode и /help.
    """
    if message.from_user is None or message.text is None:
        return
    await db.ensure_user(message.from_user.id)
    user_id = message.from_user.id

    if message.text == BTN_SOLVE:
        await message.answer(SOLVE_HINT_TEXT)
        return

    if message.text == BTN_SETTINGS:
        from .settings import cmd_mode

        await cmd_mode(message, db=db)
        return

    if message.text == BTN_HELP:
        from .start import cmd_help

        await cmd_help(message)
        return

    if message.text == BTN_STATS:
        from .start import cmd_stats

        await cmd_stats(message, db=db)
        return

    if message.text == BTN_CLEAR:
        await db.clear_history(user_id)
        await message.answer(
            "🧹 Контекст диалога очищен. Следующее задание будет решаться «с чистого листа»."
        )


# ------------------------------------------------- inline-навигация под ответом

@router.callback_query(F.data.startswith(f"{CB_MENU_PREFIX}:"))
async def cb_menu(callback: CallbackQuery, db: Database) -> None:
    """Кнопки «Меню / Настройки / Очистить / Статистика» под сообщением с решением.

    Ответ задачи не редактируем — шлём отдельное сообщение, чтобы решение
    оставалось в истории чата нетронутым.
    """
    if callback.data is None or callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка", show_alert=True)
        return

    action = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    await db.ensure_user(user_id)

    if action == "home":
        await callback.message.answer(MENU_TEXT, reply_markup=main_menu_kb())
        await callback.answer()
    elif action == "settings":
        from .settings import SETTINGS_TEXT, settings_keyboard

        mode, reply_image = await db.get_user(user_id)
        await callback.message.answer(
            SETTINGS_TEXT,
            reply_markup=settings_keyboard(mode, reply_image),
        )
        await callback.answer("Настройки открыты")
    elif action == "clear":
        await db.clear_history(user_id)
        await callback.answer("🧹 Контекст очищен", show_alert=True)
    elif action == "stats":
        from .start import build_stats_text

        await callback.message.answer(await build_stats_text(db, user_id))
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
