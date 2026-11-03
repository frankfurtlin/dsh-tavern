"""DSH plugin trusted auth: the host injects x-dsh-trusted after its login gate."""
import pytest
from sqlalchemy import select

from app.config import Settings, settings
from app.db import SessionLocal
from app.models import DeviceSession, User, UserSettings, now
from app.security import password_hasher

BASE = "/api/reader/v1"
HEADER = "x-dsh-trusted"
SECRET = "trusted-test-secret"
PASSWORD = "trusted-password-123"


@pytest.fixture
def trusted_identity(monkeypatch):
    monkeypatch.setattr(settings, "trusted_auth", True)
    monkeypatch.setattr(settings, "trusted_username", "trusted_one")
    monkeypatch.setattr(settings, "trusted_secret", SECRET)
    with SessionLocal() as db:
        user = User(username="trusted_one", password_hash=password_hasher.hash(PASSWORD))
        db.add(user)
        db.flush()
        db.add(UserSettings(owner_id=user.id))
        db.commit()
        user_id = user.id
    return user_id


def web_sessions(user_id):
    with SessionLocal() as db:
        return list(db.scalars(select(DeviceSession).where(
            DeviceSession.owner_id == user_id, DeviceSession.client_type == "web")))


def test_valid_trusted_header_authenticates_and_reuses_one_web_session(client, trusted_identity):
    first = client.get(BASE + "/auth/me", headers={HEADER: SECRET})
    assert first.status_code == 200, first.text
    assert first.json()["username"] == "trusted_one"
    session_id = first.json()["sessionId"]

    second = client.get(BASE + "/auth/me", headers={HEADER: SECRET})
    assert second.status_code == 200, second.text
    assert second.json()["sessionId"] == session_id

    sessions = web_sessions(trusted_identity)
    assert [session.id for session in sessions] == [session_id]
    assert sessions[0].revoked_at is None


def test_wrong_trusted_header_falls_back_to_the_previous_auth_flow(client, trusted_identity):
    response = client.get(BASE + "/auth/me", headers={HEADER: "not-the-secret"})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    assert web_sessions(trusted_identity) == []


def test_wrong_trusted_header_still_allows_a_valid_bearer_token(client, trusted_identity):
    login = client.post(BASE + "/auth/login", json={
        "username": "trusted_one", "password": PASSWORD,
        "clientType": "android", "deviceName": "test-device"})
    assert login.status_code == 200, login.text
    token = login.json()["accessToken"]

    response = client.get(BASE + "/auth/me", headers={"Authorization": "Bearer " + token, HEADER: "wrong"})
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "trusted_one"

    # Trusted mode did not apply, so logout keeps revoking the bearer session.
    logout = client.post(BASE + "/auth/logout", headers={"Authorization": "Bearer " + token, HEADER: "wrong"})
    assert logout.status_code == 200
    assert logout.json() == {"revoked": True}


def test_trusted_auth_disabled_is_a_regression_free_noop(client, trusted_identity, monkeypatch):
    monkeypatch.setattr(settings, "trusted_auth", False)
    response = client.get(BASE + "/auth/me", headers={HEADER: SECRET})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    assert web_sessions(trusted_identity) == []


def test_missing_trusted_user_is_a_structured_503_without_creating_an_account(client, trusted_identity, monkeypatch):
    monkeypatch.setattr(settings, "trusted_username", "trusted_absent")
    response = client.get(BASE + "/auth/me", headers={HEADER: SECRET})
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "TRUSTED_USER_UNAVAILABLE"
    assert body["retryable"] is True
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.username == "trusted_absent")) is None


def test_trusted_refresh_returns_a_bootstrap_and_does_not_set_a_cookie(client, trusted_identity):
    response = client.post(BASE + "/auth/refresh", json={}, headers={HEADER: SECRET})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accessToken"]
    assert body["user"] == {"id": trusted_identity, "username": "trusted_one"}
    assert body["sessionId"] == body["bootstrap"]["user"]["sessionId"]
    assert body["bootstrap"]["user"]["id"] == trusted_identity
    assert "reader_refresh" not in response.cookies

    # The stable session's access token works without the trusted header.
    me = client.get(BASE + "/auth/me", headers={"Authorization": "Bearer " + body["accessToken"]})
    assert me.status_code == 200, me.text
    assert me.json()["sessionId"] == body["sessionId"]


def test_trusted_refresh_works_without_cookie_or_body_token(client, trusted_identity):
    response = client.post(BASE + "/auth/refresh", json={}, headers={HEADER: SECRET, "Origin": "http://127.0.0.1:3080"})
    assert response.status_code == 200, response.text
    assert client.cookies.get("reader_refresh") is None


def test_trusted_logout_is_a_noop_and_keeps_the_stable_session(client, trusted_identity):
    me = client.get(BASE + "/auth/me", headers={HEADER: SECRET})
    assert me.status_code == 200, me.text
    session_id = me.json()["sessionId"]

    logout = client.post(BASE + "/auth/logout", headers={HEADER: SECRET})
    assert logout.status_code == 204
    assert logout.content == b""

    again = client.get(BASE + "/auth/me", headers={HEADER: SECRET})
    assert again.status_code == 200, again.text
    assert again.json()["sessionId"] == session_id
    with SessionLocal() as db:
        session = db.get(DeviceSession, session_id)
        assert session.revoked_at is None
        assert session.expires_at > now()


def test_trusted_config_reads_dsh_prefixed_environment(monkeypatch):
    monkeypatch.setenv("DSH_TRUSTED_AUTH", "1")
    monkeypatch.setenv("DSH_TRUSTED_USERNAME", "frankslin")
    monkeypatch.setenv("DSH_TRUSTED_SECRET", SECRET)
    loaded = Settings(_env_file=None, jwt_signing_secret="test", provider_master_key="test")
    assert loaded.trusted_auth is True
    assert loaded.trusted_username == "frankslin"
    assert loaded.trusted_secret == SECRET
