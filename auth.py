import os
from datetime import UTC, datetime, timedelta

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

import models
from database import get_db

# Security settings
# The signing secret lives in .env (server.py imports this module before it loads .env, so load it here).
load_dotenv()
_secret = os.getenv("JWT_SECRET_KEY")
if not _secret or len(_secret) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY is missing or too short in .env. Generate one with: "
        'python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )
SECRET_KEY: str = _secret
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """Decodes the JWT, verifies it, and returns the active database User."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if not isinstance(user_id_str, str):
            raise credentials_exception
        token_user_id = int(user_id_str)
    except (InvalidTokenError, ValueError):
        raise credentials_exception from None
        
    user = db.query(models.User).filter(models.User.id == token_user_id).first()
    if user is None:
        raise credentials_exception
        
    return user