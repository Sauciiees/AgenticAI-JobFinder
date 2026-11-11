"""Deleting user data: "Delete my account", and the daily cleanup that enforces retention.

Retention (config.py):
- guest accounts are deleted GUEST_TTL_HOURS after they were created
- CVs are deleted DATA_RETENTION_DAYS after upload
- chats idle for DATA_RETENTION_DAYS are deleted, with the agent's saved state
- expired rate-limit counters are dropped
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import config
from agent import vector_store
from database import utcnow
from models import ChatSession, UsageCounter, User
from storage import get_storage, user_prefix

log = logging.getLogger(__name__)


def _delete_threads(thread_ids: list[str]) -> None:
    if not thread_ids:
        return
    from agent.graph import delete_threads

    delete_threads(thread_ids)


def delete_user_everything(db: Session, user: User) -> None:
    """Removes the user's files, the agent's saved chat state, then the user row (which cascades to
    applications, chats and CV chunks)."""
    thread_ids = list(db.scalars(select(ChatSession.session_id).where(ChatSession.user_id == user.id)))
    get_storage().delete_prefix(user_prefix(user.id))
    _delete_threads(thread_ids)
    # SQLite can hand this id to the next new user; don't let them inherit today's counters.
    db.execute(delete(UsageCounter).where(UsageCounter.key.like(f"%:user:{user.id}")))
    db.delete(user)
    db.commit()


def delete_cv(db: Session, user: User) -> None:
    if user.cv_key:
        get_storage().delete(user.cv_key)
    user.cv_key = None
    user.cv_filename = None
    user.cv_uploaded_at = None
    db.commit()
    vector_store.delete_user_documents(user.id)


def run_cleanup(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    counts = {"guests": 0, "cvs": 0, "chats": 0, "counters": 0}

    guest_cutoff = now - timedelta(hours=config.GUEST_TTL_HOURS)
    for guest in db.scalars(select(User).where(User.is_guest.is_(True), User.created_at < guest_cutoff)).all():
        delete_user_everything(db, guest)
        counts["guests"] += 1

    data_cutoff = now - timedelta(days=config.DATA_RETENTION_DAYS)
    for user in db.scalars(select(User).where(User.cv_key.is_not(None), User.cv_uploaded_at < data_cutoff)).all():
        delete_cv(db, user)
        counts["cvs"] += 1

    old_chats = db.scalars(select(ChatSession).where(ChatSession.updated_at < data_cutoff)).all()
    _delete_threads([c.session_id for c in old_chats])
    for chat in old_chats:
        db.delete(chat)
        counts["chats"] += 1
    db.commit()

    result = db.execute(delete(UsageCounter).where(UsageCounter.expires_at < int(time.time())))
    counts["counters"] = result.rowcount or 0  # type: ignore[attr-defined]
    db.commit()

    log.info("Cleanup removed %s", counts)
    return counts
