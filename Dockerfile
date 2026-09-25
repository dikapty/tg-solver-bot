# Telegram-бот «Решатель задач»
# Образ: python:3.11-slim + шрифты DejaVu (для рендера ответов в PNG)

FROM python:3.11-slim

# Небуферизованный вывод логов, без .pyc-файлов
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Шрифты DejaVu нужны сервису render.py (ответ картинкой)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости — слой кэшируется отдельно от кода
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# Непривилегированный пользователь: данные и логи пишутся в /app/data и /app/logs
RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /app/data /app/logs \
    && chown -R botuser:botuser /app
USER botuser

# Каталог SQLite — монтируется volume из docker-compose
VOLUME ["/app/data"]

# Порт нужен только в режиме webhook (USE_WEBHOOK=true)
EXPOSE 8080

CMD ["python", "-m", "bot.main"]
