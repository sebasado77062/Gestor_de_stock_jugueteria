# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Carpeta para la base sqlite local de idempotencia (se monta como volumen en compose)
RUN mkdir -p /app/data

# Puerto del healthcheck HTTP (GET /health), ver health.py y docker-compose.yml
EXPOSE 8003

CMD ["python", "worker.py"]
