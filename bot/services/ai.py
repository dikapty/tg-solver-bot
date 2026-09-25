"""Сервис обращения к ИИ-провайдеру: запросы к модели с ретраями и обработкой ошибок.

Поддерживаются два провайдера (переключаются PROVIDER в .env):
- gemini — Google Gemini API, есть бесплатный тариф (по умолчанию);
- anthropic — Anthropic Claude, платный.

Внутренний формат сообщений — Anthropic Messages API (его же хранит БД);
для Gemini сообщения конвертируются на лету.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
from dataclasses import dataclass

from ..config import Config
from ..prompts import MODE_INSTRUCTIONS, MODE_TITLES, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Максимум токенов в ответе модели (решения задач бывают длинными)
MAX_OUTPUT_TOKENS = 16000
GEMINI_MAX_OUTPUT_TOKENS = 8192

# Число попыток при временных сбоях (rate limit, overload, timeout, 5xx)
MAX_ATTEMPTS = 3

# Базовая задержка exponential backoff, секунд
BASE_BACKOFF_DELAY = 2.0


class AIError(Exception):
    """Непоправимая ошибка обращения к ИИ (текст — сообщение для пользователя)."""


class AIRateLimited(AIError):
    """Исчерпаны попытки ретраев из-за временных ограничений API."""


@dataclass(frozen=True)
class AIResponse:
    """Нормализованный ответ модели."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str


def make_image_block(media_type: str, data: bytes) -> dict:
    """Сформировать блок изображения (base64) во внутреннем формате Messages API."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


def _append_instruction(content: list[dict] | str, instruction: str) -> list[dict] | str:
    """Добавить текстовый блок с требованием режима к сообщению пользователя."""
    if isinstance(content, str):
        return f"{content}\n\n{instruction}"
    return [*content, {"type": "text", "text": instruction}]


def _mode_instruction(mode: str) -> str:
    """Строка с требованием режима ответа для последнего сообщения пользователя."""
    mode_text = MODE_INSTRUCTIONS.get(mode, MODE_INSTRUCTIONS["detailed"])
    mode_title = MODE_TITLES.get(mode, mode)
    return f"[РЕЖИМ ОТВЕТА: {mode_title}. {mode_text}]"


def _build_messages(
    history: list[dict], content: list[dict] | str, mode: str
) -> list[dict]:
    """Собрать список сообщений (внутренний формат Messages API) с инструкцией режима."""
    messages = list(history) + [{"role": "user", "content": content}]
    messages[-1] = {
        "role": "user",
        "content": _append_instruction(messages[-1]["content"], _mode_instruction(mode)),
    }
    return messages


class AIService:
    """Единый фасад провайдеров ИИ: выбор бэкенда по конфигу, ретраи, понятные ошибки."""

    def __init__(self, config: Config) -> None:
        self._provider = config.provider
        self._model = config.model_name
        if config.provider == "anthropic":
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
        else:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=config.gemini_api_key,
                http_options=types.HttpOptions(timeout=120_000),  # миллисекунды
            )

    async def close(self) -> None:
        if self._provider == "gemini":
            # Асинхронный клиент Gemini закрывается отдельно от синхронного close()
            await self._client.aio.aclose()
        else:
            await self._client.close()

    async def solve(
        self,
        mode: str,
        history: list[dict],
        content: list[dict] | str,
    ) -> AIResponse:
        """Отправить задание модели и вернуть текстовый ответ.

        history — предыдущие сообщения диалога (формат Messages API),
        content — текущее сообщение пользователя: строка или список блоков
        (текст + base64-изображения).
        """
        messages = _build_messages(history, content, mode)
        if self._provider == "anthropic":
            return await self._solve_anthropic(messages)
        return await self._solve_gemini(messages)

    # ------------------------------------------------------------------ Gemini

    async def _solve_gemini(self, messages: list[dict]) -> AIResponse:
        from google.genai import errors, types

        contents = self._to_gemini_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
        )

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                )
                text = self._gemini_extract_text(response)
                if not text:
                    raise AIError("Модель вернула пустой ответ. Попробуйте переформулировать задание.")
                usage = response.usage_metadata
                return AIResponse(
                    text=text,
                    input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                    output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                    model=response.model_version or self._model,
                )
            except AIError:
                raise
            except errors.APIError as exc:
                code = getattr(exc, "code", 0) or 0
                if code == 429 or code >= 500:
                    last_error = exc
                    delay = await self._sleep_backoff(attempt, type(exc).__name__)
                    if delay is None:
                        break
                    continue
                if code in (400, 401, 403):
                    logger.error("Gemini API отклонил запрос (HTTP %d): %s", code, exc)
                    raise AIError("Сервис временно недоступен (ошибка конфигурации API). Сообщите администратору.")
                logger.error("Ошибка Gemini API (HTTP %d): %s", code, exc)
                raise AIError("Запрос отклонён сервисом ИИ. Попробуйте отправить задание в другом виде.")
            except Exception as exc:
                # Сетевые сбои (httpx, таймауты, обрывы соединения) — ретрай
                import httpx

                if isinstance(exc, httpx.HTTPError):
                    last_error = exc
                    delay = await self._sleep_backoff(attempt, type(exc).__name__)
                    if delay is None:
                        break
                    continue
                raise

        raise AIRateLimited(
            "Сервис ИИ сейчас перегружен или ограничил количество запросов. "
            "Пожалуйста, попробуйте ещё раз через пару минут."
        ) from last_error

    def _to_gemini_contents(self, messages: list[dict]) -> list:
        """Конвертировать сообщения внутреннего формата в Content-объекты Gemini."""
        from google.genai import types

        contents = []
        for message in messages:
            role = "model" if message["role"] == "assistant" else "user"
            content = message["content"]
            if isinstance(content, str):
                parts = [types.Part(text=content)]
            else:
                parts = []
                for block in content:
                    if block.get("type") == "text":
                        parts.append(types.Part(text=block["text"]))
                    elif block.get("type") == "image":
                        source = block.get("source", {})
                        parts.append(
                            types.Part(
                                inline_data=types.Blob(
                                    mime_type=source.get("media_type", "image/jpeg"),
                                    data=base64.b64decode(source.get("data", "")),
                                )
                            )
                        )
            contents.append(types.Content(role=role, parts=parts))
        return contents

    @staticmethod
    def _gemini_extract_text(response) -> str:
        """Собрать текст ответа Gemini из частей первого кандидата."""
        texts = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "text", None):
                    texts.append(part.text)
            break  # учитываем только первого кандидата
        return "\n".join(texts).strip()

    # --------------------------------------------------------------- Anthropic

    async def _solve_anthropic(self, messages: list[dict]) -> AIResponse:
        import anthropic

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                )
                text = "\n".join(
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                ).strip()
                if not text:
                    raise AIError("Модель вернула пустой ответ. Попробуйте переформулировать задание.")
                return AIResponse(
                    text=text,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=response.model,
                )
            except AIError:
                raise
            except (
                anthropic.RateLimitError,
                anthropic.APITimeoutError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError,
            ) as exc:
                last_error = exc
                delay = await self._sleep_backoff(attempt, type(exc).__name__)
                if delay is None:
                    break
                continue
            except anthropic.AuthenticationError:
                logger.error("Неверный ANTHROPIC_API_KEY")
                raise AIError("Сервис временно недоступен (ошибка конфигурации API). Сообщите администратору.")
            except anthropic.BadRequestError as exc:
                logger.error("BadRequestError от API: %s", exc)
                raise AIError("Запрос отклонён сервисом ИИ. Попробуйте отправить задание в другом виде.")
            except anthropic.APIStatusError as exc:
                logger.error("Ошибка API (HTTP %d): %s", exc.status_code, exc)
                raise AIError("Сервис ИИ временно недоступен. Попробуйте ещё раз позже.")

        raise AIRateLimited(
            "Сервис ИИ сейчас перегружен или ограничил количество запросов. "
            "Пожалуйста, попробуйте ещё раз через пару минут."
        ) from last_error

    # ------------------------------------------------------------------ общее

    async def _sleep_backoff(self, attempt: int, error_name: str) -> float | None:
        """Подождать перед повтором; вернуть None, если попытки исчерпаны."""
        if attempt >= MAX_ATTEMPTS:
            return None
        delay = BASE_BACKOFF_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
        logger.warning(
            "Временная ошибка API (%s), попытка %d/%d, повтор через %.1fс",
            error_name, attempt, MAX_ATTEMPTS, delay,
        )
        await asyncio.sleep(delay)
        return delay
