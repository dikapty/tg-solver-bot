"""Админ-команды: /admin_stats, /admins, /add_admin, /remove_admin.

Доступ — только для администраторов: статических (ADMIN_IDS в окружении)
и добавленных динамически через /add_admin (хранятся в AdminStore).

Каждый админ имеет одинаковые права: статистика, безлимитные запросы к ИИ
и управление списком админов.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import Config
from ..db import Database
from ..services.admin_store import AdminStore

logger = logging.getLogger(__name__)

router = Router(name="admin")


async def _ensure_admin(message: Message, store: AdminStore) -> bool:
    """Проверить, что команда пришла от админа; иначе — отказать и вернуть False."""
    if message.from_user is None:
        return False
    if store.is_admin(message.from_user.id, message.from_user.username):
        return True
    logger.warning(
        "Попытка доступа к админ-команде от не-админа: id=%d username=%s",
        message.from_user.id, message.from_user.username,
    )
    await message.answer("⛔ Эта команда доступна только администраторам.")
    return False


@router.message(Command("admin_stats"))
async def cmd_admin_stats(
    message: Message, db: Database, config: Config, admin_store: AdminStore
) -> None:
    """Сводная статистика бота для администраторов."""
    if not await _ensure_admin(message, admin_store):
        return

    total_users = await db.total_users()
    requests_today = await db.count_requests_today_all()

    await message.answer(
        "📈 Статистика бота\n"
        f"• Пользователей всего: {total_users}\n"
        f"• Запросов к ИИ сегодня (UTC): {requests_today}\n"
        f"• Модель: {config.model_name}\n"
        f"• Дневной лимит на пользователя: {config.daily_limit} (админы — без лимита)\n"
        f"• Режим запуска: {'webhook' if config.use_webhook else 'long polling'}"
    )


@router.message(Command("admins"))
async def cmd_admins(message: Message, admin_store: AdminStore) -> None:
    """Список всех администраторов (статических и добавленных)."""
    if not await _ensure_admin(message, admin_store):
        return

    ids, usernames = admin_store.snapshot()
    lines: list[str] = ["👑 Администраторы бота:"]

    if admin_store.static_ids:
        lines.append("• Из ADMIN_IDS (ID): " + ", ".join(str(i) for i in admin_store.static_ids))
    if admin_store.static_usernames:
        lines.append(
            "• Из ADMIN_IDS (username): "
            + ", ".join(f"@{u}" for u in admin_store.static_usernames)
        )
    if ids:
        lines.append("• Добавленные (ID): " + ", ".join(str(i) for i in ids))
    if usernames:
        lines.append("• Добавленные (username): " + ", ".join(f"@{u}" for u in usernames))
    if len(lines) == 1:
        lines.append("• пока пусто")

    lines.append("")
    lines.append("Все админы равны в правах: /admin_stats, безлимитные запросы к ИИ,")
    lines.append("добавление/удаление админов (/add_admin, /remove_admin).")
    if not admin_store.remote_enabled:
        lines.append("⚠️ Постоянное хранение не настроено: добавленные админы сбросятся при перезапуске.")
    await message.answer("\n".join(lines))


@router.message(Command("add_admin"))
async def cmd_add_admin(
    message: Message, command: CommandObject, admin_store: AdminStore
) -> None:
    """Добавить администратора: /add_admin @username или /add_admin 123456789."""
    if not await _ensure_admin(message, admin_store):
        return

    entry = _parse_entry(command.args if command.args else "")
    if entry is None:
        await message.answer(
            "Использование:\n"
            "/add_admin @username — добавить по username\n"
            "/add_admin 123456789 — добавить по числовому ID"
        )
        return

    status = await admin_store.add(entry)
    label = f"@{entry}" if isinstance(entry, str) else str(entry)
    if status == "added":
        logger.info("Админ добавлен: %s (добавил id=%d)", label, message.from_user.id if message.from_user else 0)
        await message.answer(
            f"✅ {label} теперь администратор.\n"
            "Права: /admin_stats, /admins, /add_admin, /remove_admin, запросы к ИИ без дневного лимита.\n"
            "Список сохранён в репозитории — переживёт перезапуск бота."
        )
    elif status == "added_local":
        await message.answer(
            f"⚠️ {label} добавлен в админы, но сохранить в репозиторий не удалось — "
            "права действуют до перезапуска бота (проверьте ADMINS_REPO_TOKEN)."
        )
    elif status == "already":
        await message.answer(f"ℹ️ {label} уже администратор.")
    else:
        await message.answer("😥 Не удалось добавить админа, попробуйте ещё раз.")


@router.message(Command("remove_admin"))
async def cmd_remove_admin(
    message: Message, command: CommandObject, admin_store: AdminStore
) -> None:
    """Убрать динамического администратора: /remove_admin @username или ID."""
    if not await _ensure_admin(message, admin_store):
        return

    entry = _parse_entry(command.args if command.args else "")
    if entry is None:
        await message.answer(
            "Использование:\n"
            "/remove_admin @username\n"
            "/remove_admin 123456789"
        )
        return

    status = await admin_store.remove(entry)
    label = f"@{entry}" if isinstance(entry, str) else str(entry)
    if status == "removed":
        logger.info("Админ удалён: %s (удалил id=%d)", label, message.from_user.id if message.from_user else 0)
        await message.answer(f"✅ {label} больше не администратор (список сохранён в репозитории).")
    elif status == "removed_local":
        await message.answer(
            f"⚠️ {label} лишён прав, но сохранить в репозиторий не удалось — "
            "изменение действует до перезапуска бота."
        )
    elif status == "static":
        await message.answer(
            f"ℹ️ {label} задан через ADMIN_IDS в окружении — удалить командой нельзя. "
            "Уберите его из ADMIN_IDS (секреты репозитория на GitHub)."
        )
    elif status == "not_found":
        await message.answer(f"ℹ️ {label} не найден в списке админов.")
    else:
        await message.answer("😥 Не удалось выполнить удаление, попробуйте ещё раз.")


def _parse_entry(raw: str) -> int | str | None:
    """Разобрать аргумент команды: числовой ID или username (с «@» или без)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    name = raw.lstrip("@")
    # username Telegram: 5–32 символа, латиница/цифры/подчёркивание
    if 5 <= len(name) <= 32 and all(c.isalnum() or c == "_" for c in name):
        return name.lower()
    return None
