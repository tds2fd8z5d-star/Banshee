FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY config.yaml .

RUN useradd --create-home --uid 1000 botuser && mkdir -p /app/data && chown botuser /app/data
USER botuser

CMD ["python", "-m", "bot"]
