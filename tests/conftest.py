"""Test setup.

server.py and its imports create files relative to the working directory at import time
(data/jobfinder.db, uploads/, vector_db/), so the test session switches into a temporary
folder before any test module imports them. Fake API keys are set so nothing reaches Gemini
or Serper (load_dotenv() never overrides variables that are already set).
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


def pytest_sessionstart(session):
    # Runs after pytest has resolved which test files to collect, but before importing them.
    os.chdir(tempfile.mkdtemp(prefix="jobfinder-tests-"))


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate limiters are in-memory module globals; clear them so tests don't affect each other."""
    import server

    for limiter in (server.login_ip_limiter, server.login_user_limiter, server.signup_ip_limiter):
        limiter.hits.clear()
    yield


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    """Uploading a CV embeds it with Gemini. Replace that with a no-op that records the call."""
    import server

    calls: list[dict] = []

    def fake_parse(file_path, user_id, doc_type="resume"):
        calls.append({"file_path": file_path, "user_id": user_id, "doc_type": doc_type})
        return ["chunk"]

    monkeypatch.setattr(server, "parse_and_store_document", fake_parse)
    return calls


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import server

    return TestClient(server.app)


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
        token = res.json()["access_token"]
        return username, password, {"Authorization": f"Bearer {token}"}

    return _make
