import jwt
import pytest

import auth


def test_health(client):
    assert client.get("/api/health").status_code == 200


def test_signup_then_login_returns_token(client):
    res = client.post("/api/signup", json={"username": "alice_auth", "password": "a-long-password"})
    assert res.status_code == 200

    res = client.post("/api/login", json={"username": "alice_auth", "password": "a-long-password"})
    assert res.status_code == 200
    body = res.json()
    assert "access_token" not in body and "hashed_password" not in body
    token = res.cookies.get("jf_session")
    payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
    assert payload["sub"] == str(body["user_id"])


def test_duplicate_username_rejected(client):
    client.post("/api/signup", json={"username": "dupe_user", "password": "a-long-password"})
    res = client.post("/api/signup", json={"username": "dupe_user", "password": "another-password"})
    assert res.status_code == 400


@pytest.mark.parametrize(
    "username,password",
    [
        ("ab", "a-long-password"),  # username too short
        ("bad name!", "a-long-password"),  # illegal characters
        ("shortpw", "short"),  # password under 8 characters
        ("samepass", "samepass"),  # password equals username
        ("longpw", "x" * 73),  # over bcrypt's 72-byte limit
    ],
)
def test_signup_validation(client, username, password):
    res = client.post("/api/signup", json={"username": username, "password": password})
    assert res.status_code == 400


def test_wrong_password_is_401(client, make_user):
    username, _, _ = make_user()
    res = client.post("/api/login", json={"username": username, "password": "wrong-password"})
    assert res.status_code == 401


def test_unknown_user_is_401(client):
    res = client.post("/api/login", json={"username": "nobody_here", "password": "whatever-123"})
    assert res.status_code == 401


def test_protected_route_requires_token(client):
    assert client.get("/api/profile").status_code == 401


def test_protected_route_rejects_forged_token(client, make_user):
    make_user()
    forged = jwt.encode({"sub": "1"}, "not-the-real-secret-but-long-enough-0000", algorithm="HS256")
    res = client.get("/api/profile", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401


def test_protected_route_rejects_token_for_deleted_user(client):
    token = auth.create_access_token({"sub": "999999"})
    res = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_profile_with_valid_token(client, make_user):
    username, _, headers = make_user()
    res = client.get("/api/profile", headers=headers)
    assert res.status_code == 200


def test_password_is_hashed_with_bcrypt():
    hashed = auth.get_password_hash("a-long-password")
    assert hashed != "a-long-password"
    assert hashed.startswith("$2")
    assert auth.verify_password("a-long-password", hashed)
    assert not auth.verify_password("wrong", hashed)
