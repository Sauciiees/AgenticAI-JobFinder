"""Test setup.

The suite runs against a fresh database built by the real migrations:
- default: a temporary SQLite file
- TEST_DATABASE_URL=postgresql://...: that Postgres (CI uses a pgvector container)
- TEST_POSTGRES=1: an embedded Postgres 16 + pgvector started from the `pgserver` package

Everything runs inside a temporary folder. Fake API keys are set so nothing reaches Gemini or
Serper (load_dotenv() never overrides variables that are already set).
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-that-is-at-least-32-characters-long"
os.environ["GOOGLE_API_KEY"] = "fake-google-key-for-tests"
os.environ["SERPER_API_KEY"] = "fake-serper-key-for-tests"
os.environ["APP_ENV"] = "test"
os.environ["STORAGE_BACKEND"] = "local"
for _var in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "SENTRY_DSN", "PROXY_SHARED_SECRET",
             "S3_BUCKET", "ENABLE_BROWSER_AUTOMATION"):
    os.environ.pop(_var, None)

_pg_server = None


def pytest_sessionstart(session):
    # Runs after pytest has resolved which test files to collect, but before importing them.
    global _pg_server
    workdir = tempfile.mkdtemp(prefix="jobfinder-tests-")
    os.chdir(workdir)

    if os.getenv("TEST_DATABASE_URL"):
        os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    elif os.getenv("TEST_POSTGRES") == "1":
        import pgserver

        # initdb occasionally fails on Windows (file locks); retry in a fresh folder.
        for attempt in range(3):
            try:
                _pg_server = pgserver.get_server(os.path.join(workdir, f"pg{attempt}"), cleanup_mode="stop")
                break
            except Exception:
                if attempt == 2:
                    raise
        os.environ["DATABASE_URL"] = _pg_server.get_uri()
    else:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(workdir, 'test.db').as_posix()}"
    os.environ["CHECKPOINT_DB"] = str(Path(workdir, "checkpoints.sqlite"))
    os.environ["UPLOAD_DIR"] = str(Path(workdir, "uploads"))

    import db_migrate

    db_migrate.migrate()


def pytest_report_header(config):
    url = os.getenv("TEST_DATABASE_URL") or ("embedded postgres" if os.getenv("TEST_POSTGRES") == "1" else "sqlite")
    return f"test database: {url.split('@')[-1]}"


@pytest.fixture(autouse=True)
def _reset_limits():
    """Rate limits and quotas are counted in the database; clear them so tests don't affect each other."""
    from database import SessionLocal
    from models import UsageCounter

    with SessionLocal() as db:
        db.query(UsageCounter).delete()
        db.commit()
    yield


@pytest.fixture(autouse=True)
def _pinned_limit_clock(monkeypatch):
    """Counters use fixed windows; pin their clock to the middle of an hour so no window ends mid-test."""
    import time

    import limits

    now = time.time()
    pinned = now - now % 3600 + 1800
    monkeypatch.setattr(limits, "_clock", lambda: pinned)


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch):
    """Replace Gemini embeddings with a deterministic bag-of-letters vector, so retrieval is testable offline."""
    from agent import vector_store

    def vec(text: str) -> list[float]:
        v = [0.0] * 26
        for ch in text.lower():
            if "a" <= ch <= "z":
                v[ord(ch) - 97] += 1.0
        return v

    monkeypatch.setattr(vector_store, "embed_documents", lambda texts: [vec(t) for t in texts])
    monkeypatch.setattr(vector_store, "embed_query", vec)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import server

    # https so the Secure session cookies are sent back, as a browser would.
    return TestClient(server.app, base_url="https://testserver")


_counter = {"n": 0}


@pytest.fixture
def make_user(client):
    """Signs up a fresh user and returns (username, password, auth headers)."""

    def _make(password: str = "correct-horse-battery"):
        _counter["n"] += 1
        username = f"user{_counter['n']}_{os.getpid()}"
        res = client.post("/api/signup", json={"username": username, "password": password})
        assert res.status_code == 200, res.text
        res = client.post("/api/login", json={"username": username, "password": password})
        assert res.status_code == 200, res.text
        # Tests juggle several users on one client, so they authenticate with a Bearer header
        # rather than the (single) cookie jar. The cookie flow has its own tests.
        client.cookies.clear()
        from auth import token_for
        from database import SessionLocal
        from models import User

        with SessionLocal() as db:
            token = token_for(db.get(User, res.json()["user_id"]))
        return username, password, {"Authorization": f"Bearer {token}"}

    return _make
