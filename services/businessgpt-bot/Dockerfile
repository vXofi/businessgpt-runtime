FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/businessgpt-bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/businessgpt-bot/bot ./bot

CMD ["python", "-m", "bot.main"]
