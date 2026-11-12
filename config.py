"""All runtime settings, read once from the environment (and .env when present).

Import this module before anything that reads settings: it calls load_dotenv() first, so the
order of imports elsewhere no longer matters. load_dotenv() never overrides variables that are
already set, so real environment variables (Render, CI, tests) always win over .env.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.strip() else default


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip() or "sqlite:///./data/jobfinder.db"
    # Hosted Postgres providers hand out postgres:// or postgresql:// URLs; use the psycopg 3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


# "local" for a run on your own machine, "production" for the hosted public demo.
APP_ENV = os.getenv("APP_ENV", "local").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

DATABASE_URL = _database_url()
IS_POSTGRES = DATABASE_URL.startswith("postgresql")
# Local runs keep LangGraph checkpoints in their own SQLite file; Postgres runs keep them in Postgres.
CHECKPOINT_DB = os.getenv("CHECKPOINT_DB", "data/checkpoints.sqlite")

# Real browser automation (Playwright job-board searches, sign-in windows, auto-apply).
# Off for the public demo, which has no browser; on for your own local Docker build.
ENABLE_BROWSER_AUTOMATION = _bool("ENABLE_BROWSER_AUTOMATION", False)

# CV storage: "local" keeps files under UPLOAD_DIR; "database" keeps them in the database (the free
# demo on Neon); "s3" uses any S3-compatible bucket (Cloudflare R2, Supabase Storage, AWS S3).
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower()
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "") or None
S3_REGION = os.getenv("S3_REGION", "auto")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
SIGNED_URL_SECONDS = _int("SIGNED_URL_SECONDS", 300)

MAX_CV_BYTES = _int("MAX_CV_BYTES", 5 * 1024 * 1024)

# Auth cookie. Browsers accept Secure cookies on http://localhost, so this stays on locally too.
COOKIE_SECURE = _bool("COOKIE_SECURE", True)
SESSION_COOKIE = "jf_session"
CSRF_COOKIE = "jf_csrf"
ACCESS_TOKEN_MINUTES = _int("ACCESS_TOKEN_MINUTES", 24 * 60)

# Shared secret that the Cloudflare Pages proxy sends, so the API can trust the client IP it forwards.
PROXY_SHARED_SECRET = os.getenv("PROXY_SHARED_SECRET", "")
# Shared secret for the daily /internal/cleanup call from GitHub Actions.
CLEANUP_SECRET = os.getenv("CLEANUP_SECRET", "")

# Cloudflare Turnstile. Leave both empty locally to skip the check.
TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")

# Limits (all counted in the database, so restarts don't reset them).
USER_DAILY_AGENT_RUNS = _int("USER_DAILY_AGENT_RUNS", 10)
GLOBAL_DAILY_AGENT_RUNS = _int("GLOBAL_DAILY_AGENT_RUNS", 60)
LOGIN_PER_IP_PER_MINUTE = _int("LOGIN_PER_IP_PER_MINUTE", 5)
LOGIN_PER_USER_PER_5_MIN = _int("LOGIN_PER_USER_PER_5_MIN", 8)
SIGNUP_PER_IP_PER_HOUR = _int("SIGNUP_PER_IP_PER_HOUR", 3)
GUEST_PER_IP_PER_HOUR = _int("GUEST_PER_IP_PER_HOUR", 5)

# Retention
GUEST_TTL_HOURS = _int("GUEST_TTL_HOURS", 24)
DATA_RETENTION_DAYS = _int("DATA_RETENTION_DAYS", 30)

# Sentry (optional). The frontend DSN is public by design; it only allows sending events.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_FRONTEND_DSN = os.getenv("SENTRY_FRONTEND_DSN", "")

# Docker only: link to the noVNC view of the browser that automation drives.
BROWSER_VIEWER_URL = os.getenv("BROWSER_VIEWER_URL") or None

# Link shown to visitors next to the auto-apply dry run.
DEMO_VIDEO_URL = os.getenv("DEMO_VIDEO_URL", "")
