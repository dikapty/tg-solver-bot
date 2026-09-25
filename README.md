# Telegram-бот «Решатель задач» (aiogram 3 + Anthropic Claude)

Бот-репетитор: пользователь присылает задание (текст, фото, фото с подписью,
документ-изображение или альбом из нескольких фото), бот отправляет его в Claude
и возвращает подробное решение с пояснениями.

## Возможности

- **Главное меню**: постоянная клавиатура под полем ввода («📥 Решить задачу», «⚙️ Настройки», «📊 Статистика», «🧹 Очистить контекст», «❓ Помощь»), команда `/menu` и inline-кнопки под каждым ответом ИИ (🏠 Меню / ⚙️ Настройки / 🧹 Очистить / 📊 Статистика) — после решения можно вернуться в меню одним нажатием. Официальное меню команд Telegram (синяя кнопка «Menu») публикуется при старте.
- **Все форматы входа**: текст, фото, фото+подпись, jpg/png «как файл», альбом (media group) — собирается в один запрос.
- **Режимы ответа** (`/mode`, inline-кнопки): «Кратко», «Подробно», «Как объяснить ребёнку». Выбор хранится в SQLite.
- **Ответ картинкой**: опция в `/mode` — рендер ответа в PNG (Pillow, DejaVu, перенос строк). По умолчанию выключена.
- **Память диалога**: последние 10 сообщений; можно уточнять («объясни шаг 3»). `/clear` сбрасывает контекст.
- **Длинные ответы** автоматически режутся на сообщения ≤ 4096 символов по границам абзацев (блоки кода не разрываются).
- **Индикатор работы**: «typing» во время запроса, «upload_photo» при отправке картинки.
- **Защита**: дневной лимит на пользователя (`DAILY_LIMIT`; **админы — без лимита**), один одновременный запрос на пользователя (asyncio.Lock + антиспам-интервал), белый список админов (`/admin_stats`, `/admins`, `/add_admin`).
- **Изображения** перед отправкой в API сжимаются до ≤1568 px по длинной стороне, JPEG quality 85.
- **Ретраи**: 3 попытки с экспоненциальной задержкой на rate limit / overload / timeout / 5xx; пользователю — понятное сообщение.
- **Логи** в stdout и файл (`bot.log`, ротация 5 МБ × 3), токены и API-ключи маскируются.
- **Два режима запуска**: long polling (по умолчанию) и webhook (aiohttp).

## Структура проекта

```
tg-solver-bot/
├── bot/
│   ├── main.py              # точка входа: polling или webhook
│   ├── config.py            # чтение .env, dataclass Config
│   ├── prompts.py           # системный промт и режимы ответа
│   ├── db.py                # SQLite (aiosqlite): users, history, requests
│   ├── keyboards.py         # reply-меню, inline-навигация, список команд
│   ├── utils.py             # логирование + разбиение длинных сообщений
│   ├── handlers/
│   │   ├── start.py         # /start /help /clear /stats
│   │   ├── menu.py          # /menu, кнопки reply-меню, inline-навигация под ответами
│   │   ├── settings.py      # /mode + inline-кнопки, ответ картинкой
│   │   ├── admin.py         # /admin_stats /admins /add_admin /remove_admin
│   │   └── tasks.py         # текст, фото, документы, альбомы → ИИ → ответ
│   └── services/
│       ├── admin_store.py   # динамический список админов (хранение в репозитории)
│       ├── ai.py            # Anthropic SDK, ретраи, обработка ошибок
│       ├── image.py         # сжатие изображений (Pillow)
│       ├── render.py        # рендер ответа в PNG
│       └── limiter.py       # антиспам: 1 одновременный запрос на пользователя
├── deploy/
│   └── tg-solver-bot.service  # systemd unit (альтернатива Docker)
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## 1. Получить токен бота (@BotFather)

1. Откройте в Telegram [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Придумайте имя и username (заканчивается на `bot`, например `my_solver_bot`).
3. BotFather выдаст токен вида `1234567890:AAE...` — это `BOT_TOKEN`.

## 2. Получить API-ключ Anthropic

1. Зарегистрируйтесь на [console.anthropic.com](https://console.anthropic.com).
2. Пополните баланс (Settings → Billing).
3. Settings → API Keys → **Create Key**. Скопируйте ключ `sk-ant-...` — это `ANTHROPIC_API_KEY`.

## 3. Локальный запуск

```bash
cd tg-solver-bot
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # и заполните BOT_TOKEN, ANTHROPIC_API_KEY
python -m bot.main
```

Бот запустится в режиме long polling — можно сразу писать ему в Telegram.

## Переменные окружения (.env)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — (обязательно) | Токен от @BotFather |
| `PROVIDER` | `gemini` | Провайдер ИИ: `gemini` (бесплатный) или `anthropic` |
| `GEMINI_API_KEY` | — (для gemini) | Ключ Google AI Studio: https://aistudio.google.com/apikey |
| `ANTHROPIC_API_KEY` | — (для anthropic) | Ключ API Anthropic |
| `MODEL_NAME` | пусто | Пусто — модель по умолчанию провайдера (`gemini-3.6-flash` / `claude-sonnet-5`) |
| `DAILY_LIMIT` | `20` | Запросов к ИИ в сутки на пользователя |
| `ADMIN_IDS` | пусто | Админы через запятую: числовой ID и/или username (`@user` или `user`); доступ к `/admin_stats` |
| `DB_PATH` | `data/bot.db` | Путь к файлу SQLite |
| `USE_WEBHOOK` | `false` | `true` — режим webhook |
| `WEBHOOK_URL` | пусто | Полный https-URL, например `https://bot.example.com/webhook` |
| `WEBHOOK_PORT` | `8080` | Порт aiohttp-сервера |
| `WEBHOOK_SECRET` | пусто | Секретный заголовок Telegram для проверки запросов |

Свой числовой ID можно узнать у [@userinfobot](https://t.me/userinfobot).

## 4. Деплой на VPS (Ubuntu 22.04+) — Docker

```bash
# Установка Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # перезайдите в сессию

# Код
git clone <ваш-репозиторий> /opt/tg-solver-bot
cd /opt/tg-solver-bot
cp .env.example .env
nano .env                        # заполните секреты
chmod 600 .env                   # ключи не должны читаться всеми

# Запуск
docker compose up -d --build
docker compose logs -f           # смотреть логи
```

База SQLite живёт в именованном volume `bot-data` и переживает обновления контейнера.

### Webhook на VPS (опционально)

По умолчанию используется polling — внешний IP и домен не нужны. Для webhook:

1. Настройте домен + reverse proxy (nginx/caddy) с TLS на порт `8080`.
2. В `.env`: `USE_WEBHOOK=true`, `WEBHOOK_URL=https://ваш-домен/webhook`, `WEBHOOK_SECRET=<случайная строка>`.
3. `docker compose up -d --build`.

Пример сервера nginx:

```nginx
server {
    listen 443 ssl;
    server_name bot.example.com;
    ssl_certificate     /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    location /webhook {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 4b. Деплой на VPS без Docker (systemd)

```bash
sudo adduser --system --group --home /opt/tg-solver-bot tgbot
sudo git clone <ваш-репозиторий> /opt/tg-solver-bot
cd /opt/tg-solver-bot
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env && sudo chmod 600 .env
sudo mkdir -p data
sudo chown -R tgbot:tgbot /opt/tg-solver-bot

sudo cp deploy/tg-solver-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tg-solver-bot
sudo systemctl status tg-solver-bot      # проверка
journalctl -u tg-solver-bot -f           # логи
```

## 4c. GitHub Actions (рекомендуемый бесплатный хостинг 24/7)

Бот запускается как job workflow `.github/workflows/bot.yml` на серверах GitHub
(США — Telegram API и Gemini API там доступны без VPN). Для публичных
репозиториев минуты Actions бесплатны и не ограничены; карта не нужна.

Как это работает:
- workflow стартует по расписанию каждые 5 часов и вручную (`workflow_dispatch`);
- job живёт до 6 часов (лимит GitHub), новый запуск отменяет предыдущий —
  бот перезапускается сам, разрыв меньше минуты;
- секреты (`BOT_TOKEN`, `GEMINI_API_KEY`) хранятся в настройках репозитория.

Установка:

```bash
# 1. создайте публичный репозиторий на github.com и запушьте проект
git init && git add -A && git commit -m "tg solver bot"
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main

# 2. в репозитории: Settings → Secrets and variables → Actions → New repository secret
#    добавьте BOT_TOKEN и GEMINI_API_KEY

# 3. запустите: вкладка Actions → «tg-bot» → Run workflow
```

Дальше перезапуск автоматический по расписанию. Логи — во вкладке Actions.

> Ограничение: история диалогов и настройки пользователей (SQLite) живут только
> внутри одного запуска и сбрасываются при перезапуске раз в ~5 часов. Для
> личного бота это почти незаметно: задачи решаются как обычно.

## 4d. Railway / Render

Оба хостинга собирают проект по `Dockerfile` — дополнительных настроек сборки не нужно.
Бесплатные тарифы засыпают без активности, а Render недоступен из РФ без VPN.

**Railway:**
1. `railway init` (или «New Project → Deploy from GitHub repo» на [railway.app](https://railway.app)).
2. Variables → добавьте все переменные из `.env` (`BOT_TOKEN`, `GEMINI_API_KEY`, ...).
3. Если нужен webhook: Railway выдаст публичный домен — пропишите его в `WEBHOOK_URL` и поставьте `USE_WEBHOOK=true`. Для polling домен не нужен.
4. Для сохранения SQLite между деплоями подключите Volume и укажите `DB_PATH` внутрь него (например `/data/bot.db`). Без volume БД сбрасывается при каждом деплое.

**Render:**
1. [render.com](https://render.com) → New → **Background Worker** (для polling) или **Web Service** (для webhook).
2. Runtime: Docker (Render найдёт `Dockerfile`) или Python (прочитает `runtime.txt`).
3. Environment — те же переменные; для webhook задайте `WEBHOOK_URL=https://<ваш-сервис>.onrender.com/webhook`.
4. Disk → добавьте диск и укажите `DB_PATH=/opt/render/project/src/data/bot.db`, чтобы SQLite не терялась.

## 5. Обновление бота

**Docker:**
```bash
cd /opt/tg-solver-bot
git pull
docker compose up -d --build      # пересоберёт образ и перезапустит контейнер
```

**Systemd:**
```bash
cd /opt/tg-solver-bot
git pull
sudo .venv/bin/pip install -r requirements.txt   # если менялись зависимости
sudo systemctl restart tg-solver-bot
```

База (настройки, история, статистика) при обновлении сохраняется: она лежит
в volume `bot-data` (Docker) или в каталоге `data/` (systemd).

## Команды бота

| Команда | Что делает |
|---|---|
| `/start` | Приветствие и инструкция (+ показывает клавиатуру меню) |
| `/menu` | Главное меню (кнопки под полем ввода) |
| `/help` | Справка |
| `/mode` | Выбор режима ответа + «ответ картинкой» (кнопки) |
| `/clear` | Сброс контекста диалога |
| `/stats` | Ваша статистика (запросы, режим) |
| `/admin_stats` | Админы: число пользователей, запросы за день |
| `/admins` | Админы: список всех администраторов |
| `/add_admin @user` или `/add_admin 123` | Админы: добавить администратора |
| `/remove_admin @user` или `/remove_admin 123` | Админы: убрать динамического админа |

## Администраторы

Админы задаются переменной `ADMIN_IDS` (числовые ID и/или username через запятую)
и имеют равные права:

- `/admin_stats`, `/admins`, `/add_admin`, `/remove_admin`;
- **запросы к ИИ без дневного лимита** (`DAILY_LIMIT` на админов не действует).

Админы, добавленные командой `/add_admin` во время работы, сохраняются в файл
`admins.json` в репозитории (через GitHub API) — это единственный способ пережить
перезапуски на бесплатном хостинге (GitHub Actions очищает рабочую директорию
примерно раз в 5 часов). Для сохранения нужен секрет `ADMINS_REPO_TOKEN`
(PAT с правом `repo`); переменные `ADMINS_REPO` и `ADMINS_FILE` задаются в workflow.
Без токена добавленные админы живут только до перезапуска (бот предупреждает в логах).

| Переменная | По умолчанию | Описание |
|---|---|---|
| `ADMINS_REPO_TOKEN` | пусто | GitHub PAT с правом `repo` для записи `admins.json` |
| `ADMINS_REPO` | пусто | Репозиторий `owner/name`, где хранить список админов |
| `ADMINS_FILE` | `admins.json` | Имя файла со списком (в корне репозитория) |

## Примечания

- Граница суток для `DAILY_LIMIT` — 00:00 UTC.
- Формулы в ответах — читаемым Unicode-текстом (x², √3, π), без сырого LaTeX; код — в блоках ```.
- Нечитаемое фото: модель честно скажет об этом и попросит переслать снимок, а не будет угадывать.
- Секреты в логах маскируются (`***BOT-TOKEN***`, `***API-KEY***`).
