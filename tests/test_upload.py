import os

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _new_session(client, headers):
    res = client.post("/api/chat-sessions", json={"title": "t"}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["session_id"]


def _upload(client, headers, name, data, content_type="application/pdf"):
    return client.post(
        "/api/upload-resume",
        params={"session_id": "s"},
        files={"file": (name, data, content_type)},
        headers=headers,
    )


def test_upload_requires_login(client):
    res = _upload(client, {}, "cv.pdf", MINIMAL_PDF)
    assert res.status_code == 401


def test_valid_pdf_is_stored_and_indexed(client, make_user, _no_embeddings):
    _, _, headers = make_user()
    res = _upload(client, headers, "My CV.pdf", MINIMAL_PDF)
    assert res.status_code == 200, res.text

    assert len(_no_embeddings) == 1
    stored = _no_embeddings[0]["file_path"]
    assert os.path.exists(stored)
    assert stored.endswith("My CV.pdf")

    res = client.get("/api/user/cv", headers=headers)
    assert res.status_code == 200
    assert res.content == MINIMAL_PDF


def test_non_pdf_rejected(client, make_user):
    _, _, headers = make_user()
    res = _upload(client, headers, "cv.docx", b"PK\x03\x04", content_type="application/msword")
    assert res.status_code == 400


def test_fake_pdf_rejected(client, make_user):
    _, _, headers = make_user()
    res = _upload(client, headers, "cv.pdf", b"<html>not a pdf</html>")
    assert res.status_code == 400


def test_oversized_pdf_rejected(client, make_user, monkeypatch):
    import server

    monkeypatch.setattr(server, "MAX_CV_BYTES", 100)
    _, _, headers = make_user()
    res = _upload(client, headers, "cv.pdf", MINIMAL_PDF + b"0" * 200)
    assert res.status_code == 413


def test_path_traversal_filename_stays_in_user_folder(client, make_user, _no_embeddings):
    _, _, headers = make_user()
    res = _upload(client, headers, "../../../etc/evil.pdf", MINIMAL_PDF)
    assert res.status_code == 200, res.text
    stored = os.path.abspath(_no_embeddings[-1]["file_path"])
    uploads_root = os.path.abspath("uploads")
    assert stored.startswith(uploads_root + os.sep)
    assert os.path.basename(stored) == "evil.pdf"


def test_users_cannot_read_each_others_cv(client, make_user):
    _, _, alice = make_user()
    _, _, bob = make_user()
    assert _upload(client, alice, "alice.pdf", MINIMAL_PDF).status_code == 200
    # Bob has no CV of his own; he must not get Alice's.
    assert client.get("/api/user/cv", headers=bob).status_code == 404
