import pytest
from fastapi import HTTPException

import server


def test_limiter_blocks_after_max_hits():
    limiter = server._RateLimiter(max_hits=3, window_seconds=60)
    for _ in range(3):
        limiter.check("1.2.3.4")
    with pytest.raises(HTTPException) as exc:
        limiter.check("1.2.3.4")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_limiter_keys_are_independent():
    limiter = server._RateLimiter(max_hits=1, window_seconds=60)
    limiter.check("a")
    limiter.check("b")  # a different key is not blocked


def test_login_is_rate_limited_per_username(client, make_user):
    username, _, _ = make_user()
    limit = server.login_user_limiter.max_hits
    # make_user already logged in once
    for _ in range(limit - 1):
        client.post("/api/login", json={"username": username, "password": "wrong-password"})
    res = client.post("/api/login", json={"username": username, "password": "wrong-password"})
    assert res.status_code == 429


def test_signup_is_rate_limited_per_ip(client):
    limit = server.signup_ip_limiter.max_hits
    for i in range(limit):
        client.post("/api/signup", json={"username": f"rl_signup_{i}", "password": "a-long-password"})
    res = client.post("/api/signup", json={"username": "rl_signup_extra", "password": "a-long-password"})
    assert res.status_code == 429
