"""Регистрация всех роутеров (handlers) бота."""

from __future__ import annotations

from aiogram import Router

from . import admin, settings, start, tasks


def get_routers() -> list[Router]:
    """Вернуть роутеры в порядке приоритета регистрации.

    Порядок важен: команды и настройки регистрируются раньше основного
    обработчика сообщений, чтобы /start, /mode и т.д. не попали в «решатель».
    """
    return [
        start.router,
        settings.router,
        admin.router,
        tasks.router,
    ]
