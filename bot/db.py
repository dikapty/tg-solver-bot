"""Слой работы с SQLite через aiosqlite.

Таблицы:
- users: настройки пользователей (режим ответа, ответ картинкой)
- history: последние сообщения диалога (контекст для модели)
- requests: журнал запросов (дневной лимит и статистика)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

import aiosqlite

from .prompts import MODE_DEFAULT

logger = logging.getLogger(__name__)

# Сколько последних сообщений пользователя храним для контекста диалога
HISTORY_LIMIT = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    mode        TEXT NOT NULL DEFAULT 'detailed',
    reply_image INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id);

CREATE TABLE IF NOT EXISTS requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    day        TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_day ON requests(day);
CREATE INDEX IF NOT EXISTS idx_requests_user_day ON requests(user_id, day);
"""


def _utc_now() -> str:
    """Текущее время в ISO-формате (UTC)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    """Текущая дата в UTC (граница дневного лимита)."""
    return datetime.now(timezone.utc).date().isoformat()


class Database:
    """Обёртка над aiosqlite: все запросы к БД в одном месте."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Открыть соединение и создать схему."""
        try:
            # Гарантируем наличие каталога для файла БД (например, data/)
            parent = os.path.dirname(os.path.abspath(self._path))
            os.makedirs(parent, exist_ok=True)
            self._conn = await aiosqlite.connect(self._path)
        except (OSError, sqlite3.Error) as exc:
            # Каталог может быть недоступен на запись (например, смонтированный
            # /data на хостинге без persistent storage) — откат на локальный путь
            logger.warning(
                "Не удалось открыть БД %s (%s) — переключаюсь на резервный путь data/bot.db",
                self._path, exc,
            )
            self._path = "data/bot.db"
            os.makedirs("data", exist_ok=True)
            self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected: вызовите connect() в on_startup")
        return self._conn

    # ------------------------------------------------------------------ users

    async def ensure_user(self, user_id: int) -> None:
        """Завести запись пользователя, если её ещё нет (idempotent)."""
        now = _utc_now()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, mode, reply_image, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (user_id, MODE_DEFAULT, now, now),
        )
        await self.conn.commit()

    async def get_user(self, user_id: int) -> tuple[str, bool]:
        """Вернуть (режим ответа, ответ_картинкой). Для неизвестного пользователя — значения по умолчанию."""
        async with self.conn.execute(
            "SELECT mode, reply_image FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return MODE_DEFAULT, False
        return str(row[0]), bool(row[1])

    async def set_mode(self, user_id: int, mode: str) -> None:
        now = _utc_now()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, mode, reply_image, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode, updated_at = excluded.updated_at
            """,
            (user_id, mode, now, now),
        )
        await self.conn.commit()

    async def set_reply_image(self, user_id: int, enabled: bool) -> None:
        now = _utc_now()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, mode, reply_image, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET reply_image = excluded.reply_image,
                                               updated_at = excluded.updated_at
            """,
            (user_id, MODE_DEFAULT, int(enabled), now, now),
        )
        await self.conn.commit()

    # ---------------------------------------------------------------- history

    async def add_history(
        self,
        user_id: int,
        role: str,
        content: list[dict] | str,
    ) -> None:
        """Добавить сообщение (user/assistant) в историю диалога.

        Контент хранится как JSON: либо строка, либо список блоков
        (текст + изображения), чтобы контекст с фото можно было переиспользовать.
        """
        payload = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        await self.conn.execute(
            "INSERT INTO history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, payload, _utc_now()),
        )
        # Обрезаем историю до HISTORY_LIMIT последних записей пользователя
        await self.conn.execute(
            """
            DELETE FROM history
            WHERE user_id = ? AND id NOT IN (
                SELECT id FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (user_id, user_id, HISTORY_LIMIT),
        )
        await self.conn.commit()

    async def get_history(self, user_id: int) -> list[dict]:
        """Вернуть последние сообщения диалога в формате Anthropic Messages API."""
        async with self.conn.execute(
            "SELECT role, content FROM history WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        messages: list[dict] = []
        for role, content in rows:
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    messages.append({"role": role, "content": parsed})
                else:
                    messages.append({"role": role, "content": str(parsed)})
            except (json.JSONDecodeError, TypeError):
                messages.append({"role": role, "content": str(content)})
        return messages

    async def clear_history(self, user_id: int) -> None:
        """Полностью сбросить контекст диалога пользователя."""
        await self.conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    # --------------------------------------------------------------- requests

    async def register_request(self, user_id: int) -> None:
        """Зарегистрировать запрос пользователя (для дневного лимита и статистики)."""
        await self.conn.execute(
            "INSERT INTO requests (user_id, day, created_at) VALUES (?, ?, ?)",
            (user_id, _today(), _utc_now()),
        )
        await self.conn.commit()

    async def count_requests_today(self, user_id: int) -> int:
        """Сколько запросов сделал пользователь сегодня (UTC)."""
        async with self.conn.execute(
            "SELECT COUNT(*) FROM requests WHERE user_id = ? AND day = ?",
            (user_id, _today()),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def count_requests_today_all(self) -> int:
        """Суммарное число запросов за сегодня (для /admin_stats)."""
        async with self.conn.execute(
            "SELECT COUNT(*) FROM requests WHERE day = ?", (_today(),)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ stats

    async def total_users(self) -> int:
        async with self.conn.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def user_total_requests(self, user_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) FROM requests WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def user_history_count(self, user_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0
