"""Основной обработчик заданий: текст, фото, фото с подписью, документы, альбомы.

Сценарий обработки:
1. Собрать вход (текст и/или изображения, альбом склеивается в один запрос).
2. Проверить дневной лимит и занятость пользователя (антиспам).
3. Скачать и сжать изображения, собрать сообщение для модели.
4. Показать chat action «typing», вызвать ИИ с ретраями.
5. Отправить ответ текстом (с разбиением по 4096 символов) или PNG-картинкой.
6. Сохранить диалог в историю (без base64-картинок, чтобы не раздувать БД).
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, Message

from ..config import Config
from ..db import Database
from ..services.ai import AIError, AIRateLimited, AIService, make_image_block
from ..services.image import ImageTooLargeError, UnsupportedImageError, prepare_image
from ..services.limiter import UserLimiter
from ..services.render import render_answer_png
from ..utils import split_message

logger = logging.getLogger(__name__)

router = Router(name="tasks")

# Максимум изображений в одном запросе к модели (альбом Telegram — до 10 фото)
MAX_IMAGES_PER_REQUEST = 10

# Сколько ждём остальные фото альбома перед обработкой, секунд
ALBUM_WAIT_SECONDS = 1.0


@dataclass
class _Deps:
    """Зависимости, нужные для обработки задания (передаются явно)."""

    bot: Bot
    db: Database
    ai: AIService
    limiter: UserLimiter
    config: Config


@dataclass
class _AlbumEntry:
    """Накопитель альбома (media group): фото + задача на отложенную обработку."""

    file_ids: list[str] = field(default_factory=list)
    caption: str = ""
    chat_id: int = 0
    user_id: int = 0
    task: asyncio.Task | None = None


class AlbumCollector:
    """Собирает сообщения media group в один запрос.

    Telegram присылает каждое фото альбома отдельным апдейтом с общим
    media_group_id. Первое фото создаёт накопитель и отложенную задачу
    (обработка через ALBUM_WAIT_SECONDS), остальные просто добавляются.
    """

    def __init__(self) -> None:
        self._groups: dict[str, _AlbumEntry] = {}
        self._lock = asyncio.Lock()

    async def add(self, message: Message, deps: _Deps) -> None:
        """Добавить фото в альбом; для первого фото — запланировать обработку."""
        group_id = message.media_group_id
        if group_id is None or message.photo is None or message.from_user is None:
            return

        async with self._lock:
            entry = self._groups.get(group_id)
            if entry is None:
                entry = _AlbumEntry(
                    chat_id=message.chat.id,
                    user_id=message.from_user.id,
                )
                self._groups[group_id] = entry
                first = True
            else:
                first = False

            # file_id самого крупного варианта фото (последний в списке)
            entry.file_ids.append(message.photo[-1].file_id)
            if message.caption and not entry.caption:
                entry.caption = message.caption

            if first:
                entry.task = asyncio.create_task(
                    self._flush_after_delay(group_id, deps),
                    name=f"album-{group_id}",
                )

    async def _flush_after_delay(self, group_id: str, deps: _Deps) -> None:
        """Подождать остальные фото альбома и запустить обработку."""
        await asyncio.sleep(ALBUM_WAIT_SECONDS)
        async with self._lock:
            entry = self._groups.pop(group_id, None)
        if entry is None:
            return
        try:
            await _process_request(
                deps,
                chat_id=entry.chat_id,
                user_id=entry.user_id,
                text=entry.caption,
                file_ids=entry.file_ids[:MAX_IMAGES_PER_REQUEST],
            )
        except Exception:
            logger.exception("Необработанная ошибка при обработке альбома %s", group_id)

    async def cancel_all(self) -> None:
        """Отменить все ожидающие задачи (при остановке бота)."""
        async with self._lock:
            for entry in self._groups.values():
                if entry.task is not None:
                    entry.task.cancel()
            self._groups.clear()


# Единственный коллектор на процесс (используется роутером, зависимости ставит main.py)
collector = AlbumCollector()


# --------------------------------------------------------------------- утилиты

async def _download_file(bot: Bot, file_id: str) -> bytes:
    """Скачать файл из Telegram по file_id."""
    buffer = io.BytesIO()
    await bot.download(file_id, destination=buffer)
    return buffer.getvalue()


async def _typing_indicator(bot: Bot, chat_id: int, action: str = ChatAction.TYPING) -> None:
    """Фоновый индикатор «печатает…» / «загружает фото»: обновляется каждые 4 секунды."""
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id, action)
            except TelegramBadRequest:
                return
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def _send_answer(bot: Bot, chat_id: int, text: str, reply_image: bool) -> None:
    """Отправить ответ: PNG-картинкой (если включена опция) или текстом с разбиением."""
    if reply_image:
        try:
            await bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
            # Рендер — CPU-задача (Pillow), выносим в отдельный поток
            png = await asyncio.to_thread(render_answer_png, text)
            await bot.send_photo(
                chat_id,
                BufferedInputFile(png, filename="answer.png"),
                caption="🖼 Ответ картинкой (отключается в /mode)",
            )
            return
        except Exception:
            logger.exception("Не удалось отправить картинку, отправляю текстом")

    chunks = split_message(text)
    if not chunks:
        chunks = ["(пустой ответ)"]
    for chunk in chunks:
        try:
            await bot.send_message(chat_id, chunk)
        except TelegramBadRequest as exc:
            # Если Telegram не принял сообщение (например, из-за разметки) — шлём обрезанное
            logger.warning("Не удалось отправить сообщение: %s", exc)
            await bot.send_message(chat_id, chunk[:4096])


def _describe_request_for_history(text: str, images_count: int) -> str:
    """Описание входящего задания для истории (без base64-картинок)."""
    parts: list[str] = []
    if images_count:
        parts.append(f"[Приложено изображений: {images_count}]")
    if text.strip():
        parts.append(text.strip())
    return "\n".join(parts) if parts else "[изображение без текста]"


# ------------------------------------------------------------- основной поток

async def _process_request(
    deps: _Deps,
    chat_id: int,
    user_id: int,
    text: str,
    file_ids: list[str],
) -> None:
    """Полный цикл обработки одного задания."""
    bot, db, limiter, config = deps.bot, deps.db, deps.limiter, deps.config

    # --- Дневной лимит -------------------------------------------------------
    requests_today = await db.count_requests_today(user_id)
    if requests_today >= config.daily_limit:
        await bot.send_message(
            chat_id,
            f"😔 Вы исчерпали дневной лимит ({config.daily_limit} запросов к ИИ в сутки). "
            "Лимит обновляется в 00:00 UTC — приходите завтра!",
        )
        return

    # --- Антиспам: один одновременный запрос на пользователя -----------------
    if limiter.is_busy(user_id):
        await bot.send_message(
            chat_id,
            "⏳ Предыдущий запрос ещё обрабатывается. Дождитесь ответа, "
            "затем пришлите следующее задание.",
        )
        return

    # Слот удерживается всё время обработки: второй параллельный запрос
    # того же пользователя подождёт здесь в очереди (asyncio.Lock)
    async with limiter.slot(user_id):
        await _handle_task(deps, chat_id, user_id, text, file_ids)


async def _handle_task(
    deps: _Deps,
    chat_id: int,
    user_id: int,
    text: str,
    file_ids: list[str],
) -> None:
    """Обработка задания внутри занятого слота: изображения → ИИ → ответ."""
    bot, db, ai = deps.bot, deps.db, deps.ai

    typing_task = asyncio.create_task(_typing_indicator(bot, chat_id))
    try:
        # --- Скачивание и сжатие изображений ----------------------------------
        content_blocks: list[dict[str, Any]] = []

        for file_id in file_ids[:MAX_IMAGES_PER_REQUEST]:
            try:
                raw = await _download_file(bot, file_id)
                prepared = await asyncio.to_thread(prepare_image, raw)
                content_blocks.append(make_image_block(prepared.media_type, prepared.data))
            except ImageTooLargeError as exc:
                await bot.send_message(chat_id, f"⚠️ {exc} Пришлите файл поменьше.")
                return
            except UnsupportedImageError as exc:
                logger.warning("Проблема с изображением от пользователя %d: %s", user_id, exc)
                await bot.send_message(
                    chat_id,
                    "🤔 Не удалось прочитать изображение: файл повреждён или это не картинка. "
                    "Пожалуйста, пришлите снимок ещё раз — в фокусе и при хорошем освещении.",
                )
                return
            except TelegramBadRequest as exc:
                logger.warning("Не удалось скачать файл: %s", exc)
                await bot.send_message(
                    chat_id,
                    "⚠️ Не удалось скачать файл из Telegram. Попробуйте прислать его ещё раз.",
                )
                return

        if text.strip():
            content_blocks.append({"type": "text", "text": text.strip()})

        if not content_blocks:
            await bot.send_message(
                chat_id,
                "Пришлите задание: текст, фото или фото с подписью. /help — справка.",
            )
            return

        # Фото без подписи — добавляем для модели явную инструкцию
        if all(block.get("type") == "image" for block in content_blocks):
            content_blocks.append(
                {"type": "text", "text": "Реши задание(я) на приложенных изображениях."}
            )

        # --- Запрос к модели ----------------------------------------------------
        history = await db.get_history(user_id)
        mode, reply_image = await db.get_user(user_id)

        try:
            result = await ai.solve(mode=mode, history=history, content=content_blocks)
        except AIRateLimited as exc:
            await bot.send_message(chat_id, str(exc))
            return
        except AIError as exc:
            await bot.send_message(chat_id, str(exc))
            return
        except Exception:
            logger.exception("Непредвиденная ошибка при обращении к ИИ (user=%d)", user_id)
            await bot.send_message(
                chat_id,
                "😥 Произошла непредвиденная ошибка — я записал её в лог. "
                "Попробуйте ещё раз чуть позже.",
            )
            return

        # --- Ответ пользователю --------------------------------------------------
        typing_task.cancel()
        await _send_answer(bot, chat_id, result.text, reply_image)

        # --- История и статистика -------------------------------------------------
        await db.add_history(user_id, "user", _describe_request_for_history(text, len(file_ids)))
        await db.add_history(user_id, "assistant", result.text)
        await db.register_request(user_id)

        logger.info(
            "Запрос решён: user=%d model=%s in=%d out=%d images=%d",
            user_id, result.model, result.input_tokens, result.output_tokens, len(file_ids),
        )
    finally:
        typing_task.cancel()


# ------------------------------------------------------------------- хэндлеры

def _deps_from_workflow(bot: Bot, db: Database, ai: AIService, limiter: UserLimiter, config: Config) -> _Deps:
    """Собрать зависимости из параметров, внедрённых aiogram из workflow_data."""
    return _Deps(bot=bot, db=db, ai=ai, limiter=limiter, config=config)


@router.message(F.photo)
async def handle_photo(
    message: Message,
    bot: Bot,
    db: Database,
    ai: AIService,
    limiter: UserLimiter,
    config: Config,
) -> None:
    """Фото (одиночное или часть альбома), возможно с подписью."""
    if message.from_user is None or message.photo is None:
        return
    deps = _deps_from_workflow(bot, db, ai, limiter, config)

    # Фото из альбома — копим и обрабатываем одним запросом
    if message.media_group_id is not None:
        await collector.add(message, deps)
        return

    await _process_request(
        deps,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        text=message.caption or "",
        file_ids=[message.photo[-1].file_id],
    )


@router.message(F.document)
async def handle_document(
    message: Message,
    bot: Bot,
    db: Database,
    ai: AIService,
    limiter: UserLimiter,
    config: Config,
) -> None:
    """Документ: принимаем только изображения (jpg/png/webp/gif, отправленные «как файл»)."""
    if message.from_user is None or message.document is None:
        return

    mime = (message.document.mime_type or "").lower()
    if not mime.startswith("image/"):
        await message.answer(
            "📎 Я умею работать только с изображениями (jpg, png, webp, gif). "
            "Пришлите картинку с заданием или скопируйте текст задания сообщением."
        )
        return

    await _process_request(
        _deps_from_workflow(bot, db, ai, limiter, config),
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        text=message.caption or "",
        file_ids=[message.document.file_id],
    )


@router.message(F.text)
async def handle_text(
    message: Message,
    bot: Bot,
    db: Database,
    ai: AIService,
    limiter: UserLimiter,
    config: Config,
) -> None:
    """Обычное текстовое задание."""
    if message.from_user is None or message.text is None:
        return

    # Сообщения, начинающиеся со «/», но не попавшие в командные роутеры,
    # считаем неизвестными командами, а не заданиями
    if message.text.startswith("/"):
        await message.answer(
            "Не знаю такой команды. Доступные команды: /start, /help, /mode, /clear, /stats."
        )
        return

    await _process_request(
        _deps_from_workflow(bot, db, ai, limiter, config),
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        text=message.text,
        file_ids=[],
    )
