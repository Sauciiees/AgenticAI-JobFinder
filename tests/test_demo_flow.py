"""The public demo end to end with a fake LLM and fake job search: guest accounts, the agent
pipeline with the auto-apply dry run, daily quotas, account deletion, retention cleanup, Turnstile,
security headers and the Serper budget."""

import json
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage

import config
from database import SessionLocal, utcnow
from models import Application, ChatSession, Document, User

FAKE_JOBS = [
    {"title": "Data Analyst", "company": "Acme", "link": "https://th.linkedin.com/jobs/view/1", "snippet": "SQL"},
    {"title": "BI Analyst", "company": "Beta", "link": "https://th.jobsdb.com/job/2", "snippet": "Power BI"},
]


class FakeLLM:
    """Answers each agent prompt with a plausible canned reply."""

    def invoke(self, messages):
        prompt = messages[-1].content
        if "intent classifier" in prompt:
            said = prompt.split('The user said: "', 1)[1].split('"', 1)[0].lower()
            return AIMessage(content="job_search" if "find" in said else "chat")
        if "short title" in prompt:
            return AIMessage(content="Data analyst search")
        if "job search assistant" in prompt:
            return AIMessage(content='{"title": "Data Analyst", "location": "Bangkok"}')
        if "Matching Agent" in prompt:
            return AIMessage(content=json.dumps([
                {"title": j["title"], "company": j["company"], "match_score": "88", "details": "Good fit",
                 "link": j["link"]} for j in FAKE_JOBS]))
        if "Career Coach" in prompt:
            return AIMessage(content="### Data Analyst at Acme\nDear hiring team...")
        if "DRY-RUN" in prompt:
            return AIMessage(content=json.dumps([
                {"title": j["title"], "company": j["company"],
                 "answers": {"Full name": "Alex Demo", "Email": "alex.demo@example.com"},
                 "steps": ["Open the posting", "Click Apply", "Upload the CV", "Submit skipped (demo)"]}
                for j in FAKE_JOBS]))
        return AIMessage(content="Happy to help with your career!")


@pytest.fixture
def fake_agent(monkeypatch):
    import agent.nodes as nodes
    import tools.job_tools as job_tools
    import tools.scraper as scraper

    monkeypatch.setattr(nodes, "llm", FakeLLM())
    monkeypatch.setattr(job_tools, "public_job_search", lambda kw, loc, tf="qdr:w": [dict(j) for j in FAKE_JOBS])
    monkeypatch.setattr(scraper, "enrich_jobs_light", lambda jobs, max_chars=4000: jobs)


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("jf_csrf")}


def _guest(client):
    res = client.post("/api/guest", json={})
    assert res.status_code == 200, res.text
    return res.json()


# --- guests -----------------------------------------------------------------------------------------

def test_guest_gets_a_seeded_demo_account(client):
    body = _guest(client)
    assert body["is_guest"] is True and body["full_name"] == "Alex Demo"
    assert body["runs_left"] == config.USER_DAILY_AGENT_RUNS

    assert client.get("/api/me").status_code == 200  # signed in by cookie
    assert len(client.get("/api/applications").json()) == 3
    cv = client.get("/api/user/cv")
    assert cv.status_code == 200 and cv.content.startswith(b"PK")
    with SessionLocal() as db:
        assert db.query(Document).filter_by(user_id=body["user_id"]).count() > 0


def test_guest_creation_is_rate_limited(client):
    for _ in range(config.GUEST_PER_IP_PER_HOUR):
        _guest(client)
    assert client.post("/api/guest", json={}).status_code == 429


def test_guest_accounts_cannot_password_login(client):
    body = _guest(client)
    res = client.post("/api/login", json={"username": body["username"], "password": "anything-at-all"})
    assert res.status_code == 401


# --- the pipeline, with the dry run instead of real auto-apply -----------------------------------------

def test_full_pipeline_dry_run_logs_applications(client, fake_agent):
    _guest(client)
    sid = client.post("/api/chat-sessions", json={"title": "New Chat"}, headers=_csrf(client)).json()["session_id"]

    res = client.post("/api/job-finder/start", json={"session_id": sid, "query": "find data analyst jobs"},
                      headers=_csrf(client))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "paused_for_selection"
    assert len(res.json()["raw_jobs"]) == 2

    res = client.post("/api/job-finder/select-jobs", json={"session_id": sid, "selected_indices": [0, 1]},
                      headers=_csrf(client))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "paused_for_review"

    res = client.post("/api/job-finder/resume",
                      json={"session_id": sid, "feedback": "approve", "jobs": FAKE_JOBS}, headers=_csrf(client))
    assert res.status_code == 200, res.text

    apps = client.get("/api/applications").json()
    assert sum(a["status"] == "Dry run (not submitted)" for a in apps) == 2

    history = client.get(f"/api/chat-sessions/{sid}/history").json()["history"]
    assert any("Dry run finished" in m["content"] and "Submit skipped (demo)" in m["content"] for m in history)

    # start + dry run = 2 runs charged
    assert client.get("/api/me").json()["runs_left"] == config.USER_DAILY_AGENT_RUNS - 2


def test_chat_message_routes_to_advisor(client, fake_agent):
    _guest(client)
    sid = client.post("/api/chat-sessions", json={"title": "x"}, headers=_csrf(client)).json()["session_id"]
    res = client.post("/api/job-finder/start", json={"session_id": sid, "query": "how is my CV?"},
                      headers=_csrf(client))
    assert res.status_code == 200
    assert res.json() == {"status": "chat_response", "chat_response": "Happy to help with your career!"}


# --- quotas -------------------------------------------------------------------------------------------

def test_user_daily_quota(client, fake_agent, monkeypatch):
    monkeypatch.setattr(config, "USER_DAILY_AGENT_RUNS", 2)
    _guest(client)
    sid = client.post("/api/chat-sessions", json={"title": "x"}, headers=_csrf(client)).json()["session_id"]
    send = lambda: client.post("/api/job-finder/start", json={"session_id": sid, "query": "hello"},  # noqa: E731
                               headers=_csrf(client))
    assert send().status_code == 200
    assert send().status_code == 200
    res = send()
    assert res.status_code == 429 and "today's 2 agent runs" in res.json()["detail"]


def test_global_daily_cap(client, fake_agent, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_DAILY_AGENT_RUNS", 1)
    _guest(client)
    sid = client.post("/api/chat-sessions", json={"title": "x"}, headers=_csrf(client)).json()["session_id"]
    assert client.post("/api/job-finder/start", json={"session_id": sid, "query": "hi"},
                       headers=_csrf(client)).status_code == 200
    client.cookies.clear()
    _guest(client)  # a different visitor
    sid2 = client.post("/api/chat-sessions", json={"title": "x"}, headers=_csrf(client)).json()["session_id"]
    res = client.post("/api/job-finder/start", json={"session_id": sid2, "query": "hi"}, headers=_csrf(client))
    assert res.status_code == 429 and "everyone" in res.json()["detail"]


def test_quotas_off_in_local_full_build(client, fake_agent, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_BROWSER_AUTOMATION", True)
    monkeypatch.setattr(config, "USER_DAILY_AGENT_RUNS", 0)
    _guest(client)
    sid = client.post("/api/chat-sessions", json={"title": "x"}, headers=_csrf(client)).json()["session_id"]
    assert client.post("/api/job-finder/start", json={"session_id": sid, "query": "hi"},
                       headers=_csrf(client)).status_code == 200


# --- deleting data ------------------------------------------------------------------------------------

def test_delete_account_needs_password_and_removes_everything(client, make_user):
    username, password, headers = make_user()
    client.post("/api/chat-sessions", json={"title": "x"}, headers=headers)
    assert client.request("DELETE", "/api/account", json={"password": "wrong-password"},
                          headers=headers).status_code == 403
    assert client.request("DELETE", "/api/account", json={"password": password}, headers=headers).status_code == 200
    with SessionLocal() as db:
        assert db.query(User).filter_by(username=username).count() == 0
    assert client.get("/api/me", headers=headers).status_code == 401


def test_guest_can_delete_without_password(client):
    body = _guest(client)
    res = client.request("DELETE", "/api/account", json={}, headers=_csrf(client))
    assert res.status_code == 200
    from storage import get_storage

    assert get_storage().get(f"user_{body['user_id']}/cv.docx") is None


def test_cleanup_requires_secret(client, monkeypatch):
    assert client.post("/internal/cleanup").status_code == 404  # no secret configured
    monkeypatch.setattr(config, "CLEANUP_SECRET", "cleanup-secret-value")
    assert client.post("/internal/cleanup", headers={"X-Cleanup-Secret": "nope"}).status_code == 404
    res = client.post("/internal/cleanup", headers={"X-Cleanup-Secret": "cleanup-secret-value"})
    assert res.status_code == 200 and res.json()["status"] == "ok"


def test_retention_deletes_old_guests_cvs_and_chats(client, make_user):
    import retention

    old_guest = _guest(client)
    _, _, headers = make_user()
    client.cookies.clear()
    fresh_guest = _guest(client)

    with SessionLocal() as db:
        long_ago = utcnow() - timedelta(days=40)
        db.get(User, old_guest["user_id"]).created_at = utcnow() - timedelta(hours=config.GUEST_TTL_HOURS + 1)
        user = db.query(User).filter(User.is_guest.is_(False)).order_by(User.id.desc()).first()
        user.cv_key, user.cv_filename, user.cv_uploaded_at = f"user_{user.id}/cv.pdf", "cv.pdf", long_ago
        db.add(ChatSession(user_id=user.id, session_id="old-chat", updated_at=long_ago))
        db.add(ChatSession(user_id=user.id, session_id="new-chat"))
        db.commit()
        user_id = user.id

        counts = retention.run_cleanup(db)

    assert counts["guests"] == 1 and counts["cvs"] == 1 and counts["chats"] == 1
    with SessionLocal() as db:
        assert db.get(User, old_guest["user_id"]) is None
        assert db.get(User, fresh_guest["user_id"]) is not None
        assert db.get(User, user_id).cv_key is None
        assert {c.session_id for c in db.query(ChatSession).filter_by(user_id=user_id)} == {"new-chat"}
        assert db.query(Application).filter_by(user_id=old_guest["user_id"]).count() == 0


# --- Turnstile, headers, Serper budget -------------------------------------------------------------------

def test_turnstile_required_when_configured(client, monkeypatch):
    import server

    monkeypatch.setattr(config, "TURNSTILE_SECRET_KEY", "secret")
    res = client.post("/api/signup", json={"username": "ts_user", "password": "a-long-password"})
    assert res.status_code == 400 and "human" in res.json()["detail"]

    async def passes(token, ip):
        assert token == "tok"

    monkeypatch.setattr(server, "_verify_turnstile", passes)
    res = client.post("/api/signup", json={"username": "ts_user", "password": "a-long-password",
                                           "turnstile_token": "tok"})
    assert res.status_code == 200


def test_security_headers(client):
    res = client.get("/api/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["Content-Security-Policy"]
    assert res.headers["Cache-Control"] == "no-store"


def test_serper_budget_stops_calls(monkeypatch):
    from tools import job_tools

    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"organic": []}

    monkeypatch.setattr(job_tools.requests, "post", lambda *a, **k: calls.append(1) or Resp())
    monkeypatch.setattr(config, "GLOBAL_DAILY_SERPER_CALLS", 2)
    for _ in range(4):
        job_tools._serper_search_structured("x", "y", "z", "P")
    assert len(calls) == 2


def test_cleanup_runs_when_the_server_starts(monkeypatch):
    """The scheduled GitHub Action only runs from the default branch; startup cleanup covers the rest."""
    import threading

    from fastapi.testclient import TestClient

    import retention
    import server

    ran = threading.Event()
    monkeypatch.setattr(retention, "run_cleanup", lambda db: ran.set() or {})
    with TestClient(server.app, base_url="https://testserver"):
        assert ran.wait(5)
