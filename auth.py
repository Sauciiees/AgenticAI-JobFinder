"""Authentication.

Browsers get the login token in an httpOnly cookie that page scripts can't read, so an XSS bug
can't steal it. Cookie-authenticated requests that change something must also send the CSRF token
(double-submit: the readable jf_csrf cookie echoed in the X-CSRF-Token header). Non-browser
clients (tests, scripts) may send the token as "Authorization: Bearer ..." instead.
"""

import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

import config
import models
from database import get_db

_secret = os.getenv("JWT_SECRET_KEY")
if not _secret or len(_secret) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY is missing or too short in .env. Generate one with: "
        'python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )
SECRET_KEY: str = _secret
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = config.ACCESS_TOKEN_MINUTES
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# A real hash to compare against when the username doesn't exist, so both cases take as long.
_DUMMY_HASH = pwd_context.hash(secrets.token_urlsafe(16))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def verify_password_or_dummy(plain_password: str, hashed_password: str | None) -> bool:
    if hashed_password is None:
        pwd_context.verify(plain_password, _DUMMY_HASH)
        return False
    return verify_password(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def set_login_cookies(response: Response, user_id: int) -> None:
    max_age = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    response.set_cookie(
        config.SESSION_COOKIE, create_access_token({"sub": str(user_id)}),
        max_age=max_age, httponly=True, secure=config.COOKIE_SECURE, samesite="strict", path="/",
    )
    response.set_cookie(
        config.CSRF_COOKIE, secrets.token_urlsafe(32),
        max_age=max_age, httponly=False, secure=config.COOKIE_SECURE, samesite="strict", path="/",
    )


def clear_login_cookies(response: Response) -> None:
    for name in (config.SESSION_COOKIE, config.CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=config.COOKIE_SECURE, samesite="strict")


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _token_from_request(request: Request) -> tuple[str | None, bool]:
    """Returns (token, came_from_cookie)."""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), False
    return request.cookies.get(config.SESSION_COOKIE), True


def _check_csrf(request: Request) -> None:
    cookie = request.cookies.get(config.CSRF_COOKIE, "")
    header = request.headers.get("X-CSRF-Token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token. Reload the page and try again.")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    """Decodes the JWT, verifies it, and returns the signed-in database User."""
    token, from_cookie = _token_from_request(request)
    if not token:
        raise _credentials_exception()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if not isinstance(user_id_str, str):
            raise _credentials_exception()
        token_user_id = int(user_id_str)
    except (InvalidTokenError, ValueError):
        raise _credentials_exception() from None

    if from_cookie and request.method not in SAFE_METHODS:
        _check_csrf(request)

    user = db.get(models.User, token_user_id)
    if user is None:
        raise _credentials_exception()
    return user
