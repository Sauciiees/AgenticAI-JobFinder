import pytest
from fastapi import HTTPException

import config
import limits
from database import SessionLocal


def test_rate_limit_blocks_after_limit():
    with SessionLocal() as db:
        for _ in range(3):
            limits.rate_limit(db, "t:block", 3, 60)
        with pytest.raises(HTTPException) as exc:
            limits.rate_limit(db, "t:block", 3, 60)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_keys_are_independent():
    with SessionLocal() as db:
        limits.rate_limit(db, "t:a", 1, 60)
        limits.rate_limit(db, "t:b", 1, 60)


def test_windows_reset():
    with SessionLocal() as db:
        assert limits.consume(db, "t:w", 60, now=1_000_020) == 1
        assert limits.consume(db, "t:w", 60, now=1_000_030) == 2
        assert limits.consume(db, "t:w", 60, now=1_000_080) == 1  # next minute


def test_remaining_does_not_consume():
    with SessionLocal() as db:
        limits.consume(db, "t:r", 86400, amount=4)
        assert limits.remaining(db, "t:r", 10, 86400) == 6
        assert limits.remaining(db, "t:r", 10, 86400) == 6


def test_login_is_rate_limited_per_username(client, make_user, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_PER_IP_PER_MINUTE", 1000)  # isolate the per-username limit
    username, _, _ = make_user()  # one login already counted
    for _ in range(config.LOGIN_PER_USER_PER_5_MIN - 1):
        client.post("/api/login", json={"username": username, "password": "wrong-password"})
    res = client.post("/api/login", json={"username": username, "password": "wrong-password"})
    assert res.status_code == 429


def test_login_is_rate_limited_per_ip(client):
    for i in range(config.LOGIN_PER_IP_PER_MINUTE):
        client.post("/api/login", json={"username": f"nobody{i}", "password": "whatever-123"})
    res = client.post("/api/login", json={"username": "nobody-extra", "password": "whatever-123"})
    assert res.status_code == 429


def test_signup_is_rate_limited_per_ip(client):
    for i in range(config.SIGNUP_PER_IP_PER_HOUR):
        client.post("/api/signup", json={"username": f"rl_signup_{i}", "password": "a-long-password"})
    res = client.post("/api/signup", json={"username": "rl_signup_extra", "password": "a-long-password"})
    assert res.status_code == 429
