"""CV chunks and their embeddings, stored in the `documents` table.

On Postgres the embedding column is pgvector and search runs in the database (cosine distance).
On SQLite (local runs, tests) embeddings are JSON lists and search runs in Python; a user's CV is
only a handful of chunks, so that is fast enough.

If Gemini embeddings are unavailable (no key, quota), chunks are still stored without an embedding
and searches fall back to returning the user's chunks in document order.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache

from sqlalchemy import delete, select

from database import SessionLocal
from models import Document

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "models/gemini-embedding-2"


@lru_cache(maxsize=1)
def _embedder():
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(model=EMBEDDING_MODEL)


def embed_documents(texts: list[str]) -> list[list[float]] | None:
    try:
        return _embedder().embed_documents(texts)
    except Exception as e:  # no key, quota exhausted, network
        log.warning("Embedding documents failed, storing chunks without embeddings: %s", e)
        return None


def embed_query(text: str) -> list[float] | None:
    try:
        return _embedder().embed_query(text)
    except Exception as e:
        log.warning("Embedding query failed, falling back to document order: %s", e)
        return None


def to_user_id(user: int | str) -> int:
    """Agent state carries users as "user_7"; the database uses 7."""
    if isinstance(user, int):
        return user
    return int(str(user).removeprefix("user_"))


def replace_user_documents(user: int | str, chunks: list[str], doc_type: str = "resume") -> int:
    """Stores a user's document chunks, replacing any earlier ones of the same type."""
    user_id = to_user_id(user)
    vectors = embed_documents(chunks) if chunks else None
    with SessionLocal() as db:
        db.execute(delete(Document).where(Document.user_id == user_id, Document.doc_type == doc_type))
        for i, chunk in enumerate(chunks):
            db.add(Document(user_id=user_id, doc_type=doc_type, chunk_index=i, content=chunk,
                            embedding=vectors[i] if vectors else None))
        db.commit()
    return len(chunks)


def get_user_documents(user: int | str, doc_type: str | None = "resume") -> list[str]:
    user_id = to_user_id(user)
    query = select(Document.content).where(Document.user_id == user_id)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    with SessionLocal() as db:
        return list(db.scalars(query.order_by(Document.chunk_index)))


def search_user_documents(user: int | str, query: str, k: int = 4, doc_type: str = "resume") -> list[str]:
    """The user's k chunks most similar to the query."""
    user_id = to_user_id(user)
    qvec = embed_query(query)
    with SessionLocal() as db:
        base = select(Document).where(Document.user_id == user_id, Document.doc_type == doc_type)
        if qvec is None:
            return [d.content for d in db.scalars(base.order_by(Document.chunk_index).limit(k))]

        if db.get_bind().dialect.name == "postgresql":
            nearest = base.where(Document.embedding.is_not(None)).order_by(
                Document.embedding.op("<=>")(qvec)  # pgvector cosine distance
            ).limit(k)
            hits = [d.content for d in db.scalars(nearest)]
        else:
            docs = [d for d in db.scalars(base) if d.embedding]
            docs.sort(key=lambda d: _cosine_distance(qvec, d.embedding or []))
            hits = [d.content for d in docs[:k]]

        if not hits:  # chunks were stored without embeddings
            hits = [d.content for d in db.scalars(base.order_by(Document.chunk_index).limit(k))]
        return hits


def delete_user_documents(user: int | str) -> None:
    with SessionLocal() as db:
        db.execute(delete(Document).where(Document.user_id == to_user_id(user)))
        db.commit()


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / (na * nb) if na and nb else 1.0
