FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY src/ ./src/

ENV PYTHONUNBUFFERED=1
ENV DEPLOYMENT_MODE=cloud
ENV DATA_DIR=/data
ENV DATABASE_PATH=/data/trading.db
ENV NGROK_ENABLED=false

RUN mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -sf http://127.0.0.1:${PORT:-8000}/api/health || exit 1

CMD ["python", "main.py"]
