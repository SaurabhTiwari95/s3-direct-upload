# --- build stage: install dependencies only -------------------------------------------------
FROM python:3.15-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- runtime stage: small image, non-root user ------------------------------------------------
FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/srv/data/uploads.db
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
 && mkdir -p /srv/data && chown app:app /srv/data
WORKDIR /srv
COPY --from=build /install /usr/local
COPY app ./app
USER app
VOLUME ["/srv/data"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status != 200)"
# One worker: the rate limiter is in memory and SQLite has one writer. See README, "How it scales".
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
