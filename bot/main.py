"""Точка входа: создание бота и диспетчера, запуск в режиме polling или webhook."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import Config, load_config
from .db import Database
from .handlers import get_routers
from .handlers.tasks import collector
from .keyboards import BOT_COMMANDS
from .services.ai import AIService
from .services.limiter import UserLimiter
from .utils import setup_logging

logger = logging.getLogger(__name__)


async def on_startup(bot: Bot) -> None:
    """Действия при старте: логируем режим и публикуем меню команд Telegram."""
    logger.info("Бот запущен: %s", await bot.get_me())
    # Синяя кнопка «Menu» в клиентах Telegram — официальный список команд
    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        logger.warning("Не удалось опубликовать меню команд", exc_info=True)


async def on_shutdown(bot: Bot, db: Database, ai: AIService) -> None:
    """Действия при остановке: закрываем ресурсы.

    aiogram внедряет сюда зависимости из workflow_data по именам параметров.
    """
    await collector.cancel_all()
    await ai.close()
    await db.close()
    logger.info("Бот остановлен, ресурсы освобождены")


def build_bot_and_dispatcher(config: Config) -> tuple[Bot, Dispatcher]:
    """Создать Bot, Dispatcher и зарегистрировать роутеры и зависимости."""
    # Parse mode не задаём: ответы содержат код и Unicode-формулы,
    # которые безопаснее всего отправлять простым текстом
    if config.telegram_proxy:
        # TELEGRAM_PROXY задан — трафик к api.telegram.org идёт через прокси
        # (нужно, когда провайдер блокирует Telegram API).
        # socks5:// требует пакет aiohttp-socks: pip install aiohttp-socks
        from aiogram.client.session.aiohttp import AiohttpSession

        logger.info("Telegram API через прокси: %s", config.telegram_proxy)
        session = AiohttpSession(proxy=config.telegram_proxy)
    else:
        session = None
    bot = Bot(token=config.bot_token, session=session) if session else Bot(token=config.bot_token)
    dp = Dispatcher()

    # Сервисы — общие на весь процесс, кладутся в workflow_data для инъекции в хэндлеры
    db = Database(config.db_path)
    ai = AIService(config)
    limiter = UserLimiter()

    dp["db"] = db
    dp["ai"] = ai
    dp["limiter"] = limiter
    dp["config"] = config

    for router in get_routers():
        dp.include_router(router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    return bot, dp


async def run_polling(bot: Bot, dp: Dispatcher) -> None:
    """Режим long polling (по умолчанию)."""
    logger.info("Запуск в режиме long polling")
    # Отключаем webhook на случай, если он был включён ранее.
    # Сбой не фатален: start_polling сам ретраит соединение с Telegram
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        logger.warning("Не удалось отключить webhook — продолжаю без этого", exc_info=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def run_health_server(port: int) -> None:
    """Мини-HTTP-сервер «живости»: отвечает OK на любой GET-запрос.

    Нужен бесплатным хостингам (например, HuggingFace Spaces), которые
    ожидают, что контейнер слушает HTTP-порт, иначе помечают приложение упавшим.
    """
    from aiohttp import web

    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", handler)
    app.router.add_get("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info("Health-сервер слушает порт %d", port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def run_webhook(bot: Bot, dp: Dispatcher, config: Config) -> None:
    """Режим webhook на встроенном aiohttp-сервере."""
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    if not config.webhook_url:
        raise RuntimeError("USE_WEBHOOK=true, но WEBHOOK_URL не задан в .env")

    logger.info("Запуск в режиме webhook: %s (порт %d)", config.webhook_url, config.webhook_port)

    await bot.set_webhook(
        url=config.webhook_url,
        secret_token=config.webhook_secret or None,
        allowed_updates=dp.resolve_used_update_types(),
    )

    app = setup_application(bot, dp)
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=config.webhook_secret or None,
    ).register(app, path="/webhook")

    from aiohttp.web import AppRunner, TCPSite

    runner = AppRunner(app)
    await runner.setup()
    site = TCPSite(runner, host="0.0.0.0", port=config.webhook_port)
    await site.start()

    # Работаем, пока процесс не остановят
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def main() -> None:
    """Инициализация и запуск."""
    setup_logging()
    config = load_config()

    bot, dp = build_bot_and_dispatcher(config)

    # Инициализируем БД до старта приёма апдейтов
    db: Database = dp.workflow_data["db"]
    await db.connect()

    try:
        if config.use_webhook:
            await run_webhook(bot, dp, config)
        elif config.health_port:
            # Параллельно с polling держим health-сервер для хостинга
            await asyncio.gather(run_polling(bot, dp), run_health_server(config.health_port))
        else:
            await run_polling(bot, dp)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Бот остановлен пользователем")
