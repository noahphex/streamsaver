FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8092

# Streamsaver keeps decrypted credentials in Flask app memory after unlock.
# A single worker avoids per-process unlock drift while threads keep UI requests responsive.
CMD ["gunicorn", "--bind", "0.0.0.0:8092", "--workers", "1", "--threads", "8", "--timeout", "120", "wsgi:app"]
