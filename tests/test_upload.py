import io


def _make_pdf(text: str) -> bytes:
    """A small but valid one-page PDF containing `text`."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


MINIMAL_PDF = _make_pdf("Jane Doe - Data Analyst - Python SQL Tableau")


def _docx_bytes(text: str) -> bytes:
    import docx

    document = docx.Document()
    for para in text.split("\n\n"):
        document.add_paragraph(para)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _upload(client, headers, name, data, content_type="application/pdf"):
    return client.post("/api/upload-resume", files={"file": (name, data, content_type)}, headers=headers)


def test_upload_requires_login(client):
    assert _upload(client, {}, "cv.pdf", MINIMAL_PDF).status_code == 401


def test_pdf_is_stored_and_downloadable(client, make_user):
    _, _, headers = make_user()
    res = _upload(client, headers, "My CV.pdf", MINIMAL_PDF)
    assert res.status_code == 200, res.text

    profile = client.get("/api/profile", headers=headers).json()
    assert profile["cv_filename"] == "My CV.pdf"

    res = client.get("/api/user/cv", headers=headers)
    assert res.status_code == 200
    assert res.content == MINIMAL_PDF
    assert res.headers["content-type"] == "application/pdf"


def test_docx_is_parsed_into_chunks(client, make_user):
    from agent import vector_store
    from database import SessionLocal
    from models import User

    username, _, headers = make_user()
    text = "Jane Doe, data analyst with Python and SQL.\n\nBuilt dashboards in Power BI for a retail chain."
    res = _upload(client, headers, "cv.docx", _docx_bytes(text),
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert res.status_code == 200, res.text

    with SessionLocal() as db:
        user = db.query(User).filter_by(username=username).one()
        assert user.cv_key == f"user_{user.id}/cv.docx"
        chunks = vector_store.get_user_documents(user.id)
    assert any("Power BI" in c for c in chunks)


def test_reupload_replaces_old_file_and_chunks(client, make_user):
    from agent import vector_store
    from database import SessionLocal
    from models import User

    username, _, headers = make_user()
    _upload(client, headers, "cv.docx", _docx_bytes("Old CV: experienced in COBOL and mainframes."),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert _upload(client, headers, "cv.pdf", MINIMAL_PDF).status_code == 200

    with SessionLocal() as db:
        user = db.query(User).filter_by(username=username).one()
        assert user.cv_key.endswith("cv.pdf")
        assert not any("COBOL" in c for c in vector_store.get_user_documents(user.id))
    from storage import get_storage

    assert get_storage().get(f"user_{user.id}/cv.docx") is None


def test_wrong_type_rejected(client, make_user):
    _, _, headers = make_user()
    assert _upload(client, headers, "cv.txt", b"just text", "text/plain").status_code == 400


def test_unreadable_pdf_rejected_not_500(client, make_user):
    _, _, headers = make_user()
    assert _upload(client, headers, "cv.pdf", b"%PDF-1.4 garbage with no structure").status_code == 400


def test_fake_pdf_rejected(client, make_user):
    _, _, headers = make_user()
    assert _upload(client, headers, "cv.pdf", b"<html>not a pdf</html>").status_code == 400


def test_zip_that_is_not_docx_rejected(client, make_user):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("evil.txt", "hi")
    _, _, headers = make_user()
    assert _upload(client, headers, "cv.docx", buf.getvalue()).status_code == 400


def test_oversized_file_rejected(client, make_user, monkeypatch):
    import server

    monkeypatch.setattr(server, "MAX_CV_BYTES", 100)
    _, _, headers = make_user()
    assert _upload(client, headers, "cv.pdf", MINIMAL_PDF + b"0" * 200).status_code == 413


def test_filename_cannot_choose_storage_location(client, make_user):
    from database import SessionLocal
    from models import User

    username, _, headers = make_user()
    assert _upload(client, headers, "../../../etc/evil.pdf", MINIMAL_PDF).status_code == 200
    with SessionLocal() as db:
        user = db.query(User).filter_by(username=username).one()
        assert user.cv_key == f"user_{user.id}/cv.pdf"
        assert user.cv_filename == "evil.pdf"


def test_users_cannot_read_each_others_cv(client, make_user):
    _, _, alice = make_user()
    _, _, bob = make_user()
    assert _upload(client, alice, "alice.pdf", MINIMAL_PDF).status_code == 200
    assert client.get("/api/user/cv", headers=bob).status_code == 404


def test_delete_cv_removes_file_and_chunks(client, make_user):
    from agent import vector_store
    from database import SessionLocal
    from models import User

    username, _, headers = make_user()
    _upload(client, headers, "cv.docx", _docx_bytes("Python developer with ten years of experience."),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert client.delete("/api/profile/cv", headers=headers).status_code == 200
    assert client.get("/api/user/cv", headers=headers).status_code == 404
    with SessionLocal() as db:
        user = db.query(User).filter_by(username=username).one()
        assert vector_store.get_user_documents(user.id) == []
