"""Админ-команды: /admin_stats (доступ только из ADMIN_IDS)."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..config import Config
from ..db import Database

logger = logging.getLogger(__name__)

router = Router(name="admin")


@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message, db: Database, config: Config) -> None:
    """Сводная статистика бота для администраторов."""
    if message.from_user is None:
        return
    if message.from_user.id not in config.admin_ids:
        logger.warning(
            "Попытка доступа к /admin_stats от не-админа: id=%d", message.from_user.id
        )
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return

    total_users = await db.total_users()
    requests_today = await db.count_requests_today_all()

    await message.answer(
        "📈 Статистика бота\n"
        f"• Пользователей всего: {total_users}\n"
        f"• Запросов к ИИ сегодня (UTC): {requests_today}\n"
        f"• Модель: {config.model_name}\n"
        f"• Дневной лимит на пользователя: {config.daily_limit}\n"
        f"• Режим запуска: {'webhook' if config.use_webhook else 'long polling'}"
    )
