FROM python:3.11-slim-bookworm

WORKDIR /app

# Build tools for pandas/numpy wheels fallback; curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-cloud.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-cloud.txt

COPY main.py .
COPY src ./src/
COPY scripts/start-web.sh ./scripts/start-web.sh
COPY scripts/start-worker.sh ./scripts/start-worker.sh

ENV PYTHONUNBUFFERED=1
ENV DEPLOYMENT_MODE=cloud
ENV DATA_DIR=/data
ENV DATABASE_PATH=/data/trading.db
ENV NGROK_ENABLED=false
ENV PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD sh -c 'curl -sf "http://127.0.0.1:${PORT:-8000}/healthz" || exit 1'

CMD ["bash", "scripts/start-web.sh"]
