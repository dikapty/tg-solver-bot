"""Команды /start, /help, /clear, /stats."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from ..db import Database, HISTORY_LIMIT
from ..keyboards import main_menu_kb
from ..prompts import MODE_TITLES
from ..services.limiter import UserLimiter

logger = logging.getLogger(__name__)

router = Router(name="start")

WELCOME_TEXT = """\
👋 Привет! Я бот-репетитор: решаю задачи с помощью ИИ.

📥 Пришлите задание в любом виде:
• текст — условие задачи или вопрос;
• фото — страница учебника, тетрадь или скриншот;
• фото с подписью — снимок + ваши пожелания;
• документ-изображение (jpg/png, отправленный «как файл»);
• альбом из нескольких фото — соберу их в один запрос.

📤 Я верну подробное решение с пояснениями и выделенным ответом.

🏠 Внизу — кнопки главного меню: «Решить задачу», «Настройки», \
«Статистика», «Очистить контекст», «Помощь». Команда /menu — показать меню ещё раз.

⚙️ Полезные команды:
/mode — выбрать формат ответа (кратко / подробно / как ребёнку);
/clear — очистить контекст диалога;
/stats — ваша статистика;
/help — повторить эту справку.

💬 После решения можно уточнять: «объясни шаг 3», «реши другим способом» — \
я помню последние {history_limit} сообщений диалога. Под каждым ответом \
будут кнопки навигации: 🏠 Меню, ⚙️ Настройки, 🧹 Очистить, 📊 Статистика.\
"""


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database) -> None:
    """Приветствие при первом запуске (+ постоянная клавиатура меню)."""
    if message.from_user is not None:
        await db.ensure_user(message.from_user.id)
        logger.info("Новый пользователь: id=%d username=%s", message.from_user.id, message.from_user.username)
    await message.answer(
        WELCOME_TEXT.format(history_limit=HISTORY_LIMIT),
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Справка (дублирует приветствие)."""
    await message.answer(
        WELCOME_TEXT.format(history_limit=HISTORY_LIMIT),
        reply_markup=main_menu_kb(),
    )


@router.message(Command("clear"))
async def cmd_clear(message: Message, db: Database, limiter: UserLimiter) -> None:
    """Сброс контекста диалога."""
    if message.from_user is None:
        return
    await db.clear_history(message.from_user.id)
    await message.answer(
        "🧹 Контекст диалога очищен. Следующее задание будет решаться «с чистого листа»."
    )


async def build_stats_text(db: Database, user_id: int) -> str:
    """Текст персональной статистики (общий для /stats и меню)."""
    await db.ensure_user(user_id)

    mode, reply_image = await db.get_user(user_id)
    total = await db.user_total_requests(user_id)
    today = await db.count_requests_today(user_id)
    history_count = await db.user_history_count(user_id)

    return (
        "📊 Ваша статистика\n"
        f"• Режим ответа: {MODE_TITLES.get(mode, mode)}\n"
        f"• Ответ картинкой: {'включён' if reply_image else 'выключен'}\n"
        f"• Запросов сегодня: {today}\n"
        f"• Запросов всего: {total}\n"
        f"• Сообщений в контексте: {history_count}/{HISTORY_LIMIT}"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database) -> None:
    """Персональная статистика пользователя."""
    if message.from_user is None:
        return
    await message.answer(await build_stats_text(db, message.from_user.id))
