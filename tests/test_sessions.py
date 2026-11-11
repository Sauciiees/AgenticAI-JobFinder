def _create(client, headers, title="My chat"):
    res = client.post("/api/chat-sessions", json={"title": title}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["session_id"]


def test_create_and_list_sessions(client, make_user):
    _, _, headers = make_user()
    sid = _create(client, headers)
    res = client.get("/api/chat-sessions", headers=headers)
    assert res.status_code == 200
    assert sid in [s["session_id"] for s in res.json()]


def test_sessions_are_private_to_their_owner(client, make_user):
    _, _, alice = make_user()
    _, _, bob = make_user()
    sid = _create(client, alice, "alice's chat")

    listed = [s["session_id"] for s in client.get("/api/chat-sessions", headers=bob).json()]
    assert sid not in listed

    assert client.get(f"/api/chat-sessions/{sid}/history", headers=bob).status_code == 404
    assert client.put(f"/api/chat-sessions/{sid}", json={"title": "pwned"}, headers=bob).status_code == 404
    assert client.delete(f"/api/chat-sessions/{sid}", headers=bob).status_code == 404

    # Alice's session is untouched.
    titles = {s["session_id"]: s["title"] for s in client.get("/api/chat-sessions", headers=alice).json()}
    assert titles[sid] == "alice's chat"


def test_cannot_resume_someone_elses_agent_session(client, make_user):
    _, _, alice = make_user()
    _, _, bob = make_user()
    sid = _create(client, alice)
    res = client.post(
        "/api/job-finder/resume",
        json={"session_id": sid, "feedback": "approve", "jobs": []},
        headers=bob,
    )
    assert res.status_code in (404, 422)
