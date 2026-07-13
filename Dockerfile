# Family Link Alerts -- application image.
#
# This is a plain Python/FastAPI app (no browser automation needed -- that
# lives entirely in the separate, unmodified upstream `familylink-auth`
# container; see docker-compose.yml and third_party/NOTICE.md).
FROM python:3.14-slim

WORKDIR /app

# Install Python deps first so they're cached independently of app code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic.ini ./

# Runs as a non-root user for defense in depth.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

ENV APP_DATA_DIR=/data \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/', timeout=5)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
