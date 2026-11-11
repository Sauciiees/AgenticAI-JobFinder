"""Vector store, storage, checkpointer and cascading deletes, on whichever database the suite runs."""

import pytest

from agent import vector_store
from database import SessionLocal
from models import Application, ChatSession, Document, User


def _user(name: str) -> int:
    with SessionLocal() as db:
        user = User(username=name, hashed_password="x")
        db.add(user)
        db.commit()
        return user.id


def test_search_returns_most_similar_chunks_for_that_user_only():
    alice = _user("vs_alice")
    bob = _user("vs_bob")
    vector_store.replace_user_documents(alice, ["python pandas numpy", "cooking recipes kitchen", "zzz yyy xxx"])
    vector_store.replace_user_documents(bob, ["python python python"])

    hits = vector_store.search_user_documents(alice, "python numpy", k=1)
    assert hits == ["python pandas numpy"]
    assert "python python python" not in vector_store.search_user_documents(alice, "python", k=5)


def test_search_falls_back_to_document_order_without_embeddings(monkeypatch):
    uid = _user("vs_noembed")
    monkeypatch.setattr(vector_store, "embed_documents", lambda texts: None)
    vector_store.replace_user_documents(uid, ["first chunk", "second chunk", "third chunk"])
    monkeypatch.setattr(vector_store, "embed_query", lambda q: [1.0] * 26)
    assert vector_store.search_user_documents(uid, "anything", k=2) == ["first chunk", "second chunk"]


def test_user_id_accepts_agent_state_format():
    uid = _user("vs_format")
    vector_store.replace_user_documents(f"user_{uid}", ["hello world chunk"])
    assert vector_store.get_user_documents(uid) == ["hello world chunk"]


def test_deleting_a_user_cascades():
    uid = _user("cascade_user")
    with SessionLocal() as db:
        db.add(Application(user_id=uid, job_title="x"))
        db.add(ChatSession(user_id=uid, session_id="cascade-session"))
        db.commit()
    vector_store.replace_user_documents(uid, ["some chunk text here"])

    with SessionLocal() as db:
        db.delete(db.get(User, uid))
        db.commit()
        assert db.query(Application).filter_by(user_id=uid).count() == 0
        assert db.query(ChatSession).filter_by(user_id=uid).count() == 0
        assert db.query(Document).filter_by(user_id=uid).count() == 0


def test_checkpoints_can_be_deleted_per_thread():
    from agent.graph import delete_threads, memory

    config = {"configurable": {"thread_id": "ckpt-test", "checkpoint_ns": ""}}
    from langgraph.checkpoint.base import empty_checkpoint

    memory.put(config, empty_checkpoint(), {"source": "input", "step": -1, "parents": {}}, {})
    assert memory.get_tuple({"configurable": {"thread_id": "ckpt-test"}}) is not None
    delete_threads(["ckpt-test"])
    assert memory.get_tuple({"configurable": {"thread_id": "ckpt-test"}}) is None


@pytest.mark.parametrize("key", ["../x", "/abs", "a/../../b", "a\\b", ""])
def test_storage_rejects_unsafe_keys(key, tmp_path):
    from storage import LocalStorage

    with pytest.raises(ValueError):
        LocalStorage(str(tmp_path)).put(key, b"x", "text/plain")


def test_local_storage_roundtrip_and_prefix_delete(tmp_path):
    from storage import LocalStorage

    st = LocalStorage(str(tmp_path))
    st.put("user_1/cv.pdf", b"data", "application/pdf")
    assert st.get("user_1/cv.pdf") == b"data"
    st.delete_prefix("user_1")
    assert st.get("user_1/cv.pdf") is None


def test_database_storage_roundtrip_and_prefix_delete():
    from storage import DatabaseStorage

    st = DatabaseStorage()
    st.put("user_901/cv.pdf", b"%PDF-1 first", "application/pdf")
    st.put("user_901/cv.pdf", b"%PDF-1 second", "application/pdf")  # overwrite
    st.put("user_9011/cv.pdf", b"other user", "application/pdf")
    assert st.get("user_901/cv.pdf") == b"%PDF-1 second"
    st.delete_prefix("user_901")
    assert st.get("user_901/cv.pdf") is None
    assert st.get("user_9011/cv.pdf") == b"other user"  # a longer id with the same start survives
    st.delete("user_9011/cv.pdf")
    assert st.get("user_9011/cv.pdf") is None


def test_upload_and_download_with_database_storage(client, make_user, monkeypatch):
    import config
    import storage
    from tests.test_upload import MINIMAL_PDF

    monkeypatch.setattr(config, "STORAGE_BACKEND", "database")
    monkeypatch.setattr(storage, "_storage", None)
    _, _, headers = make_user()
    res = client.post("/api/upload-resume", files={"file": ("cv.pdf", MINIMAL_PDF, "application/pdf")}, headers=headers)
    assert res.status_code == 200, res.text
    res = client.get("/api/user/cv", headers=headers)
    assert res.status_code == 200 and res.content == MINIMAL_PDF
    monkeypatch.setattr(storage, "_storage", None)
