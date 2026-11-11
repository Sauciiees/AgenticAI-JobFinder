"""Re-reads every stored CV and rebuilds its text chunks and embeddings.

Run once after upgrading a local install from the ChromaDB version (CV text used to live in
vector_db/; it now lives in the database), or after changing the embedding model:

    python scripts/reindex_cvs.py

Uses your GOOGLE_API_KEY for embeddings: one call per CV.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db_migrate  # noqa: E402
from agent.parser import chunk_text, detect_kind, extract_text  # noqa: E402
from agent.vector_store import replace_user_documents  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import User  # noqa: E402
from storage import get_storage  # noqa: E402


def main() -> None:
    db_migrate.migrate()
    storage = get_storage()
    with SessionLocal() as db:
        users = db.query(User).filter(User.cv_key.is_not(None)).all()
        for user in users:
            assert user.cv_key is not None
            data = storage.get(user.cv_key)
            kind = detect_kind(data) if data else None
            if not data or not kind:
                print(f"user {user.id}: CV file missing or unreadable ({user.cv_key}), skipped")
                continue
            chunks = chunk_text(extract_text(data, kind))
            replace_user_documents(user.id, chunks)
            print(f"user {user.id}: {len(chunks)} chunks indexed")
    print("Done.")


if __name__ == "__main__":
    main()
