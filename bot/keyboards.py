"""Клавиатуры и меню: постоянная reply-клавиатура, inline-навигация после
ответов, список команд бота.

Модуль намеренно не импортирует ничего из handlers, чтобы его могли
использовать все роутеры без риска циклических импортов.
"""

from __future__ import annotations

from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# --- Тексты кнопок постоянного меню (reply-клавиатура) -----------------------
BTN_SOLVE = "📥 Решить задачу"
BTN_SETTINGS = "⚙️ Настройки"
BTN_STATS = "📊 Статистика"
BTN_CLEAR = "🧹 Очистить контекст"
BTN_HELP = "❓ Помощь"

# Все тексты кнопок одним набором (для фильтра F.text.in_)
MENU_BUTTONS = frozenset({BTN_SOLVE, BTN_SETTINGS, BTN_STATS, BTN_CLEAR, BTN_HELP})

# --- Префикс callback_data для inline-навигации под ответами ----------------
CB_MENU_PREFIX = "menu"

MENU_TEXT = """\
🏠 Главное меню

📥 Решить задачу — просто пришлите текст или фото задания
⚙️ Настройки — формат ответа (кратко / подробно / как ребёнку), ответ картинкой
📊 Статистика — ваши запросы, режим и остаток лимита
🧹 Очистить контекст — начать диалог «с чистого листа»
❓ Помощь — полная справка

Можно не выбирать кнопки: просто отправьте задание — я решу его, \
а под ответом появятся кнопки навигации.
"""

SOLVE_HINT_TEXT = """\
📥 Жду задание!

Пришлите его в любом виде: текст, фото, фото с подписью, файл-картинку \
или альбом из нескольких фото.

После решения под ответом появятся кнопки: 🏠 Меню, ⚙️ Настройки, \
🧹 Очистить контекст, 📊 Статистика.
"""


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода: главное меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SOLVE)],
            [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_STATS)],
            [KeyboardButton(text=BTN_CLEAR), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Пришлите задание или выберите действие…",
    )


def post_answer_kb() -> InlineKeyboardMarkup:
    """Inline-навигация, которая прикрепляется к сообщению с решением."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data=f"{CB_MENU_PREFIX}:home",
                ),
                InlineKeyboardButton(
                    text="⚙️ Настройки",
                    callback_data=f"{CB_MENU_PREFIX}:settings",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Очистить контекст",
                    callback_data=f"{CB_MENU_PREFIX}:clear",
                ),
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data=f"{CB_MENU_PREFIX}:stats",
                ),
            ],
        ]
    )


# --- Официальное меню команд Telegram (синяя кнопка «Menu») ------------------
BOT_COMMANDS = [
    BotCommand(command="start", description="Перезапуск и приветствие"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="mode", description="Формат ответа и картинка"),
    BotCommand(command="stats", description="Моя статистика"),
    BotCommand(command="clear", description="Очистить контекст диалога"),
    BotCommand(command="help", description="Справка"),
    BotCommand(command="admin_stats", description="Статистика бота (админы)"),
    BotCommand(command="admins", description="Список админов (админы)"),
    BotCommand(command="add_admin", description="Добавить админа (админы)"),
    BotCommand(command="remove_admin", description="Убрать админа (админы)"),
]
