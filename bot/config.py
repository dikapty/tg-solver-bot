"""Конфигурация бота: все настройки читаются из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool = False) -> bool:
    """Разбор булевой переменной окружения (true/1/yes/on)."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    """Разбор целочисленной переменной окружения с откатом на значение по умолчанию."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_admin_ids() -> frozenset[int]:
    """Список ID администраторов из ADMIN_IDS (через запятую или точку с запятой)."""
    raw = os.getenv("ADMIN_IDS", "")
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.add(int(chunk))
    return frozenset(ids)


# Модель по умолчанию для каждого провайдера (используется, если MODEL_NAME пуст)
DEFAULT_MODEL_BY_PROVIDER = {
    "gemini": "gemini-3.6-flash",
    "anthropic": "claude-sonnet-5",
}


@dataclass(frozen=True)
class Config:
    """Неизменяемая конфигурация приложения."""

    bot_token: str
    provider: str
    anthropic_api_key: str
    gemini_api_key: str
    model_name: str
    daily_limit: int
    admin_ids: frozenset[int]
    db_path: str
    use_webhook: bool
    webhook_url: str
    webhook_port: int
    webhook_secret: str
    # Прокси для Telegram API (http://, https:// или socks5://); пусто — без прокси.
    # Нужен, если api.telegram.org заблокирован провайдером.
    telegram_proxy: str
    # Порт мини-сервера «живости» (GET / → OK) для хостингов вроде HuggingFace Space.
    # 0 — выключен. В режиме webhook не используется (там уже есть aiohttp-сервер).
    health_port: int


def load_config() -> Config:
    """Загрузить .env и собрать конфигурацию. Падаем сразу, если нет обязательных переменных."""
    from dotenv import load_dotenv

    # Существующие переменные окружения имеют приоритет над .env
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env и укажите токен бота."
        )

    provider = os.getenv("PROVIDER", "gemini").strip().lower() or "gemini"
    if provider not in DEFAULT_MODEL_BY_PROVIDER:
        raise RuntimeError(
            f"PROVIDER={provider!r} не поддерживается. Допустимые значения: gemini, anthropic."
        )

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if provider == "anthropic" and not anthropic_key:
        raise RuntimeError(
            "PROVIDER=anthropic, но не задан ANTHROPIC_API_KEY. Укажите ключ в .env "
            "или переключитесь на бесплатный PROVIDER=gemini."
        )
    if provider == "gemini" and not gemini_key:
        raise RuntimeError(
            "PROVIDER=gemini, но не задан GEMINI_API_KEY. Получите бесплатный ключ на "
            "https://aistudio.google.com/apikey и укажите его в .env."
        )

    default_model = DEFAULT_MODEL_BY_PROVIDER[provider]
    return Config(
        bot_token=bot_token,
        provider=provider,
        anthropic_api_key=anthropic_key,
        gemini_api_key=gemini_key,
        model_name=os.getenv("MODEL_NAME", "").strip() or default_model,
        daily_limit=max(1, _get_int("DAILY_LIMIT", 20)),
        admin_ids=_get_admin_ids(),
        db_path=os.getenv("DB_PATH", "data/bot.db").strip() or "data/bot.db",
        use_webhook=_get_bool("USE_WEBHOOK", False),
        webhook_url=os.getenv("WEBHOOK_URL", "").strip(),
        webhook_port=_get_int("WEBHOOK_PORT", 8080),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
        telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip(),
        # PaaS-хостинги (Render и подобные) сами задают порт через PORT;
        # health-сервер на нём нужен, чтобы сервис не считали упавшим/заснувшим
        health_port=_get_int("HEALTH_PORT", _get_int("PORT", 0)),
    )
