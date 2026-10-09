FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install .

COPY glossary.yaml ./

RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown bot:bot /app/data
USER bot

ENV DB_PATH=/app/data/bot.db
VOLUME ["/app/data"]

CMD ["python", "-m", "app.main"]
