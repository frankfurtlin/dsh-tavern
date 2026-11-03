import secrets
import re
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
        response.set_cookie("reader_refresh", refresh, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=settings.refresh_days * 86400, path=settings.cookie_path_prefix+"/api/reader/v1/auth")
    else:
        result["refreshToken"] = refresh
    return result


def origin_check(request):
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origins.split(","):
        raise Problem("ORIGIN_REJECTED", "请求来源不允许", 403)
    if request.cookies.get("reader_refresh") and not origin:
        raise Problem("ORIGIN_REJECTED", "浏览器刷新请求缺少来源", 403)


def current_user(request: Request, db=Depends(get_db)):
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
