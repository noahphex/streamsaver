FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

LABEL org.opencontainers.image.source="https://github.com/noahphex/streamsaver" \
    org.opencontainers.image.title="Streamsaver" \
    org.opencontainers.image.description="Self-hosted streaming subscription and viewing activity tracker" \
    org.opencontainers.image.licenses="MIT"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --uid 1000 --create-home streamsaver \
    && mkdir -p /app/config /app/data /app/assets/logos /app/backups \
    && chown -R streamsaver:streamsaver /app/config /app/data /app/assets/logos /app/backups

USER streamsaver
EXPOSE 8092
HEALTHCHECK --interval=120s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8092/health || exit 1

# Streamsaver keeps decrypted credentials in Flask app memory after unlock.
# A single worker avoids per-process unlock drift while threads keep UI requests responsive.
CMD ["gunicorn", "--bind", "0.0.0.0:8092", "--workers", "1", "--threads", "8", "--timeout", "120", "wsgi:app"]
