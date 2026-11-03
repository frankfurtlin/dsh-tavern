import secrets
import re
import hmac
import logging
import jwt
from datetime import timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, Request, Response
from sqlalchemy import select, text
from .config import settings
from .db import get_db
from .domain import digest
from .errors import Problem
from .models import User, DeviceSession, RateLimit, now

password_hasher = PasswordHasher()
logger = logging.getLogger("reader")
TRUSTED_HEADER = "x-dsh-trusted"


def check_password(user, password):
    try:
        return password_hasher.verify(user.password_hash, password)
    except VerificationError:
        return False


def password_format(password):
    if not 12 <= len(password) <= 128:
        raise Problem("PASSWORD_INVALID", "密码须为 12 至 128 个字符")


def username_format(username):
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        raise Problem("USERNAME_INVALID", "用户名须为 3 至 32 个英文字母、数字或下划线")
    return username.lower()


def rate_limit(db, key, maximum, seconds, increment=True):
    key = digest(key)
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": int(key[:15], 16)})
    row = db.get(RateLimit, key)
    if not row:
        row = RateLimit(key=key, count=0, window_at=now())
        db.add(row)
    if (now() - row.window_at).total_seconds() > seconds:
        row.count, row.window_at = 0, now()
    if row.count >= maximum:
        db.commit()
        raise Problem("RATE_LIMITED", "尝试过于频繁，请稍后再试", 429, {"retryAfter": max(1, seconds - int((now() - row.window_at).total_seconds()))}, True)
    if increment:
        row.count += 1
    db.commit()


def issue_access(session):
    return jwt.encode({"sub": session.owner_id, "sid": session.id, "exp": now() + timedelta(minutes=settings.access_minutes), "iat": now()}, settings.jwt_signing_secret, algorithm="HS256")


def issue_session(db, user, client_type, device_name, response):
    if client_type not in ("web", "android", "ios"):
        raise Problem("CLIENT_INVALID", "客户端类型无效")
    refresh = secrets.token_urlsafe(48)
    session = DeviceSession(owner_id=user.id, refresh_hash=digest(refresh), previous_hashes=[], client_type=client_type,
        device_name=device_name[:100], expires_at=now() + timedelta(days=settings.refresh_days))
    db.add(session)
    db.flush()
    from .account_bootstrap import build
    bootstrap = build(db, user, session)
    db.commit()
    return token_result(session, user, refresh, response, bootstrap=bootstrap)


def token_result(session, user, refresh, response, bootstrap=None):
    result = {"accessToken": issue_access(session), "sessionId": session.id, "user": {"id": user.id, "username": user.username}}
    if bootstrap is not None:
        result["bootstrap"] = bootstrap
    if session.client_type == "web":
        # Trusted refresh has no rotation and therefore no refresh value. Never
        # hand out a cookie the server could not verify.
        if refresh:
            response.set_cookie("reader_refresh", refresh, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=settings.refresh_days * 86400, path=settings.cookie_path_prefix+"/api/reader/v1/auth")
    elif refresh:
        result["refreshToken"] = refresh
    return result


def origin_check(request):
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origins.split(","):
        raise Problem("ORIGIN_REJECTED", "请求来源不允许", 403)
    if request.cookies.get("reader_refresh") and not origin:
        raise Problem("ORIGIN_REJECTED", "浏览器刷新请求缺少来源", 403)


def stable_web_session(db, user):
    """Reuse one long-lived web session for the DSH panel identity.

    The plugin host owns the login gate, so the app must not rotate or revoke
    this session on refresh/logout. Reusing the newest non-revoked web session
    keeps ``/auth/me`` and the sessions list stable across page reloads.
    """
    session = db.scalar(select(DeviceSession).where(
        DeviceSession.owner_id == user.id,
        DeviceSession.client_type == "web",
        DeviceSession.revoked_at.is_(None),
        DeviceSession.expires_at > now(),
    ).order_by(DeviceSession.created_at.desc()).limit(1))
    if session:
        return session
    session = DeviceSession(owner_id=user.id, refresh_hash=digest(secrets.token_urlsafe(48)),
        previous_hashes=[], client_type="web", device_name="DSH 面板",
        expires_at=now() + timedelta(days=settings.refresh_days))
    db.add(session)
    db.commit()
    return session


def trusted_identity(request: Request, db):
    """Resolve the fixed DSH user from the host-injected trusted header.

    Returns ``None`` whenever trusted auth is off, the header is absent, or the
    secret does not match, so callers fall back to the normal token flow. A
    header that does match but names no active user is a configuration error
    and surfaces as a structured 503 instead of silently creating an account.
    """
    if not settings.trusted_auth:
        return None
    supplied = request.headers.get(TRUSTED_HEADER)
    if not supplied:
        return None
    if not settings.trusted_secret:
        logger.warning("trusted auth enabled without DSH_TRUSTED_SECRET; using token auth")
        return None
    if not hmac.compare_digest(supplied.encode("utf-8"), settings.trusted_secret.encode("utf-8")):
        logger.warning("rejected trusted header with mismatched secret")
        return None
    username = settings.trusted_username.strip().lower()
    if not username:
        logger.error("trusted auth enabled without DSH_TRUSTED_USERNAME")
        raise Problem("TRUSTED_USER_UNAVAILABLE", "可信用户未配置", 503, retryable=True)
    user = db.scalar(select(User).where(User.username == username, User.status == "active"))
    if not user:
        logger.error("trusted auth user %r not found or inactive", settings.trusted_username)
        raise Problem("TRUSTED_USER_UNAVAILABLE", "可信用户不存在或不可用", 503, retryable=True)
    request.state.session = stable_web_session(db, user)
    request.state.trusted = True
    return user


def current_user(request: Request, db=Depends(get_db)):
    trusted = trusted_identity(request, db)
    if trusted is not None:
        return trusted
    token = request.headers.get("authorization", "")
    try:
        payload = jwt.decode(token.removeprefix("Bearer "), settings.jwt_signing_secret, algorithms=["HS256"])
        session = db.get(DeviceSession, payload["sid"])
        user = db.get(User, payload["sub"])
        if not session or not user or session.owner_id != user.id or session.revoked_at or session.expires_at < now() or user.status != "active":
            raise ValueError()
    except (jwt.PyJWTError, KeyError, ValueError):
        raise Problem("AUTH_REQUIRED", "登录已过期，请重新登录", 401)
    request.state.session = session
    return user
