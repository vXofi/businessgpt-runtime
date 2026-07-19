FROM python:3.11-slim@sha256:b27df5841f3355e9473f9a516d38a6783b6c8dfeacaf2d14a240f443b368ddb6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

RUN groupadd --gid 10001 businessgpt \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin businessgpt

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock

COPY bot ./bot

USER 10001:10001

CMD ["python", "-m", "bot.main"]
