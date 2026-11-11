"""Browser-style sessions (httpOnly cookie + CSRF double-submit) and the Cloudflare proxy checks."""

import config


def _signup_and_login(client, username="cookie_user", password="a-long-password"):
    client.post("/api/signup", json={"username": username, "password": password})
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res


def test_login_sets_httponly_secure_strict_cookie(client):
    res = _signup_and_login(client, "cookie_flags")
    set_cookies = res.headers.get_list("set-cookie")
    session = next(c for c in set_cookies if c.startswith("jf_session="))
    csrf = next(c for c in set_cookies if c.startswith("jf_csrf="))
    assert "HttpOnly" in session and "Secure" in session and "SameSite=strict" in session
    assert "HttpOnly" not in csrf  # the page must be able to read it to echo it back


def test_cookie_session_reads_without_csrf(client):
    _signup_and_login(client, "cookie_read")
    res = client.get("/api/me")
    assert res.status_code == 200
    assert res.json()["username"] == "cookie_read"


def test_cookie_session_writes_need_csrf_header(client):
    _signup_and_login(client, "cookie_write")
    assert client.post("/api/chat-sessions", json={"title": "x"}).status_code == 403
    assert client.post("/api/chat-sessions", json={"title": "x"},
                       headers={"X-CSRF-Token": "wrong"}).status_code == 403
    ok = client.post("/api/chat-sessions", json={"title": "x"},
                     headers={"X-CSRF-Token": client.cookies.get("jf_csrf")})
    assert ok.status_code == 200


def test_logout_clears_session(client):
    _signup_and_login(client, "cookie_logout")
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_me_requires_login(client):
    assert client.get("/api/me").status_code == 401


def test_job_board_routes_hidden_without_automation(client, make_user):
    _, _, headers = make_user()
    assert client.get("/api/job-boards", headers=headers).status_code == 404
    assert client.post("/api/job-boards/linkedin/sign-in", headers=headers).status_code == 404


def test_config_reports_public_mode(client):
    body = client.get("/api/config").json()
    assert body["automation"] is False
    assert body["browser_viewer_url"] is None


def test_proxy_secret_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(config, "PROXY_SHARED_SECRET", "s3cret-proxy-value")
    assert client.get("/api/config").status_code == 404           # direct call: hidden
    assert client.get("/api/health").status_code == 200            # Render's health check still works
    ok = client.get("/api/config", headers={"X-Proxy-Secret": "s3cret-proxy-value"})
    assert ok.status_code == 200


def test_client_ip_header_trusted_only_with_proxy_secret(client, monkeypatch):
    """Spoofing X-Client-IP without the secret must not dodge the per-IP login limit."""
    for i in range(config.LOGIN_PER_IP_PER_MINUTE):
        client.post("/api/login", json={"username": f"spoof{i}", "password": "whatever-123"},
                    headers={"X-Client-IP": f"10.0.0.{i}"})
    res = client.post("/api/login", json={"username": "spoof-x", "password": "whatever-123"},
                      headers={"X-Client-IP": "10.0.0.99"})
    assert res.status_code == 429


def test_proxy_forwarded_ip_is_used(client, monkeypatch):
    monkeypatch.setattr(config, "PROXY_SHARED_SECRET", "s3cret-proxy-value")
    for i in range(config.LOGIN_PER_IP_PER_MINUTE + 2):
        res = client.post("/api/login", json={"username": f"fwd{i}", "password": "whatever-123"},
                          headers={"X-Proxy-Secret": "s3cret-proxy-value", "X-Client-IP": f"10.1.0.{i}"})
        assert res.status_code == 401  # different real IPs: never rate limited
