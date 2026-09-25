"""Ограничения нагрузки: один одновременный запрос на пользователя + антиспам-задержка."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator


class UserLimiter:
    """Персональная блокировка и антиспам для каждого пользователя.

    - Слот `slot()` удерживается на всё время обработки задания: у одного
      пользователя выполняется не более одного запроса к ИИ одновременно,
      остальные ждут в очереди на asyncio.Lock.
    - Между стартами запросов выдерживается минимальный интервал (защита от флуда).
    """

    def __init__(self, min_interval_seconds: float = 2.0) -> None:
        self._min_interval = min_interval_seconds
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_request: dict[int, float] = {}

    def is_busy(self, user_id: int) -> bool:
        """True, если у пользователя уже обрабатывается запрос."""
        lock = self._locks.get(user_id)
        return lock is not None and lock.locked()

    @asynccontextmanager
    async def slot(self, user_id: int) -> AsyncIterator[None]:
        """Занять слот обработки до завершения задания.

        Перед началом работы выдерживает антиспам-паузу, если предыдущий
        запрос стартовал меньше min_interval назад.
        """
        lock = self._locks[user_id]
        async with lock:
            elapsed = time.monotonic() - self._last_request.get(user_id, 0.0)
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request[user_id] = time.monotonic()
            yield
