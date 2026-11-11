# Slim image for the public demo API (Render). No browser, no VNC: ENABLE_BROWSER_AUTOMATION=false.
# Your own full build with real auto-apply is backend.dockerfile (docker compose).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    ENABLE_BROWSER_AUTOMATION=false

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000

# Migrations run before the server starts, so a deploy never serves an old schema.
# --proxy-headers: Render terminates TLS in front of the app.
CMD ["sh", "-c", "python db_migrate.py && exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --workers 1"]
