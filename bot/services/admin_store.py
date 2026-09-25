"""Динамический список администраторов с хранением в файле репозитория GitHub.

Статические админы задаются переменной окружения ADMIN_IDS (ID и/или username).
Админы, добавленные командой /add_admin во время работы бота, сохраняются
в JSON-файл (по умолчанию admins.json) в репозитории бота через GitHub API —
это единственный способ пережить перезапуски на бесплатном хостинге
(GitHub Actions перезапускает раннер примерно раз в 5 часов, и локальная
SQLite при этом очищается).

Формат файла: {"ids": [123, ...], "usernames": ["name", ...]} (username —
без «@», в нижнем регистре).

Если ADMINS_REPO_TOKEN не задан, динамический список живёт только в памяти
процесса и сбрасывается при перезапуске (об этом предупреждаем в логах).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging

import aiohttp

from ..config import Config

logger = logging.getLogger(__name__)

# Ветка репозитория, в которой лежит файл со списком админов
REPO_BRANCH = "main"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


class AdminStore:
    """Список админов: окружение (ADMIN_IDS) + добавленные через /add_admin."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._ids: set[int] = set()
        self._usernames: set[str] = set()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ свойства

    @property
    def remote_enabled(self) -> bool:
        """Настроено ли постоянное хранение (токен + имя репозитория)."""
        return bool(self._config.admins_repo_token and self._config.admins_repo)

    @property
    def static_ids(self) -> list[int]:
        """Статические ID из ADMIN_IDS (отсортированные)."""
        return sorted(self._config.admin_ids)

    @property
    def static_usernames(self) -> list[str]:
        """Статические username из ADMIN_IDS (без «@», отсортированные)."""
        return sorted(self._config.admin_usernames)

    # ------------------------------------------------------------------ проверка

    def is_admin(self, user_id: int, username: str | None = None) -> bool:
        """Администратор по числовому ID или username (статический или динамический)."""
        if self._config.is_admin(user_id, username):
            return True
        if user_id in self._ids:
            return True
        if username and username.lstrip("@").lower() in self._usernames:
            return True
        return False

    def snapshot(self) -> tuple[list[int], list[str]]:
        """Текущий динамический список: (ID, username без «@»)."""
        return sorted(self._ids), sorted(self._usernames)

    # ------------------------------------------------------------------ загрузка

    async def load(self) -> None:
        """Прочитать динамический список из репозитория при старте бота."""
        if not self.remote_enabled:
            logger.warning(
                "ADMINS_REPO_TOKEN/ADMINS_REPO не заданы — динамические админы "
                "будут жить только до перезапуска"
            )
            return
        url = self._contents_url()
        try:
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                async with session.get(url, headers=self._headers()) as response:
                    if response.status == 404:
                        logger.info("Файл админов в репозитории пока не создан")
                        return
                    if response.status != 200:
                        raise RuntimeError(f"GET {url} -> HTTP {response.status}")
                    data = await response.json()
            payload = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
            self._ids.update(int(x) for x in payload.get("ids", []))
            self._usernames.update(str(x).lower() for x in payload.get("usernames", []))
            logger.info(
                "Загружены динамические админы: %d ID, %d username",
                len(self._ids), len(self._usernames),
            )
        except Exception:
            # Не фатально: бот работает со списком из окружения
            logger.warning("Не удалось загрузить список админов из репозитория", exc_info=True)

    # ------------------------------------------------------------------ изменения

    async def add(self, entry: int | str) -> str:
        """Добавить админа (int — ID, str — username без «@»).

        Возвращает статус: added | added_local | already | error.
        «added_local» — добавлен в память, но сохранить в репозиторий не вышло.
        """
        async with self._lock:
            if isinstance(entry, int):
                if entry in self._config.admin_ids or entry in self._ids:
                    return "already"
                self._ids.add(entry)
            else:
                name = entry.lower()
                if name in self._config.admin_usernames or name in self._usernames:
                    return "already"
                self._usernames.add(name)
            persisted = await self._persist()
            return "added" if persisted else "added_local"

    async def remove(self, entry: int | str) -> str:
        """Убрать динамического админа.

        Статусы: removed | removed_local | static (задан через окружение) |
        not_found | error.
        """
        async with self._lock:
            if isinstance(entry, int):
                if entry in self._config.admin_ids:
                    return "static"
                if entry not in self._ids:
                    return "not_found"
                self._ids.discard(entry)
            else:
                name = entry.lower()
                if name in self._config.admin_usernames:
                    return "static"
                if name not in self._usernames:
                    return "not_found"
                self._usernames.discard(name)
            persisted = await self._persist()
            return "removed" if persisted else "removed_local"

    # ------------------------------------------------------------------ GitHub API

    def _contents_url(self) -> str:
        return (
            f"https://api.github.com/repos/{self._config.admins_repo}"
            f"/contents/{self._config.admins_file}"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.admins_repo_token}",
            "Accept": "application/vnd.github+json",
        }

    async def _persist(self) -> bool:
        """Записать текущий список в файл репозитория. True — удалось."""
        if not self.remote_enabled:
            return False
        payload = {"ids": sorted(self._ids), "usernames": sorted(self._usernames)}
        body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        url = self._contents_url()
        try:
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                # Текущий sha файла нужен для обновления (404 — файла ещё нет)
                sha: str | None = None
                async with session.get(url, headers=self._headers()) as response:
                    if response.status == 200:
                        sha = (await response.json()).get("sha")
                    elif response.status != 404:
                        raise RuntimeError(f"GET {url} -> HTTP {response.status}")

                put_body = {
                    "message": "bot: обновлён список динамических админов",
                    "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
                    "branch": REPO_BRANCH,
                }
                if sha:
                    put_body["sha"] = sha
                async with session.put(url, headers=self._headers(), json=put_body) as response:
                    if response.status not in (200, 201):
                        text = await response.text()
                        raise RuntimeError(f"PUT {url} -> HTTP {response.status}: {text[:200]}")
            return True
        except Exception:
            logger.exception("Не удалось сохранить список админов в репозиторий")
            return False
