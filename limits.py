"""Rate limits and daily quotas, counted in the database so they survive restarts and are shared
by every server process.

Counters use fixed windows: ("login:ip:1.2.3.4", start of the current minute) -> count.
"""

from __future__ import annotations

import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from models import UsageCounter

MINUTE = 60
HOUR = 3600
DAY = 86400


# Indirection so tests can pin the clock (a window boundary mid-test would reset a counter).
_clock = time.time


def _window(window_seconds: int, now: float | None = None) -> tuple[int, int]:
    now_i = int(now if now is not None else _clock())
    start = now_i - now_i % window_seconds
    return start, now_i


def count(db: Session, key: str, window_seconds: int, now: float | None = None) -> int:
    start, _ = _window(window_seconds, now)
    value = db.scalar(select(UsageCounter.count).where(UsageCounter.key == key, UsageCounter.window_start == start))
    return value or 0


def consume(db: Session, key: str, window_seconds: int, amount: int = 1, now: float | None = None) -> int:
    """Adds `amount` to the counter for the current window and returns the new total."""
    start, _ = _window(window_seconds, now)
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    upsert = insert(UsageCounter).values(key=key, window_start=start, count=amount,
                                         expires_at=start + window_seconds)
    stmt = upsert.on_conflict_do_update(
        index_elements=[UsageCounter.key, UsageCounter.window_start],
        set_={"count": UsageCounter.count + amount},
    ).returning(UsageCounter.count)
    total = db.execute(stmt).scalar_one()
    db.commit()
    return total


def retry_after(window_seconds: int, now: float | None = None) -> int:
    start, now_i = _window(window_seconds, now)
    return max(1, start + window_seconds - now_i)


def too_many(detail: str, window_seconds: int) -> HTTPException:
    wait = retry_after(window_seconds)
    return HTTPException(status_code=429, detail=detail.format(seconds=wait),
                         headers={"Retry-After": str(wait)})


def rate_limit(db: Session, key: str, limit: int, window_seconds: int) -> None:
    """Counts one attempt; raises 429 once `limit` attempts were made in the current window."""
    if consume(db, key, window_seconds) > limit:
        raise too_many("Too many attempts. Try again in {seconds} seconds.", window_seconds)


def remaining(db: Session, key: str, limit: int, window_seconds: int) -> int:
    return max(0, limit - count(db, key, window_seconds))
