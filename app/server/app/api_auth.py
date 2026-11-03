from datetime import timedelta
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from pydantic import Field
from .db import get_db
from .config import settings
from .models import User, UserSettings, DeviceSession, Task, now
from .security import current_user, username_format, password_format, password_hasher, check_password, issue_session, origin_check, rate_limit, token_result
from .schemas import Credentials, Refresh, ReadingSettings, Strict
from .domain import digest, owned
from .errors import Problem

router = APIRouter()


@router.post("/auth/register", status_code=201)
def register(data: Credentials, request: Request, db=Depends(get_db)):
    if not settings.registration_enabled:
        raise Problem("REGISTRATION_DISABLED", "注册暂未开放", 403)
    # A quota-bound, valid invitation admits a small beta cohort behind one NAT.
    # Keep the public registration limit; quota is locked again below after the
    # rate-limit transaction commits, so concurrency cannot reuse a stale row.
    invited = None
    if data.inviteCode:
        from .foundation.models import Invite
        from .foundation.core import hash_code
        invited = db.scalar(select(Invite.id).where(Invite.code_hash == hash_code(data.inviteCode),
            Invite.disabled.is_(False), Invite.admission.is_(True), Invite.expires_at > now(), Invite.uses < Invite.max_uses))
    rate_limit(db, "register:" + request.client.host, 50 if invited else 5, 3600)
    username = username_format(data.username)
    password_format(data.password)
    if db.scalar(select(User).where(User.username == username)):
        raise Problem("REGISTRATION_FAILED", "无法使用此用户名注册")
    from .foundation.growth import admission, claim_invite
    from .foundation.events import emit
    invite = admission(db, data.inviteCode)
    user = User(username=username, password_hash=password_hasher.hash(data.password))
    db.add(user)
    db.flush()
    db.add(UserSettings(owner_id=user.id))
    db.flush()
    from .foundation.default_models import provision
    from .account_recovery import create_code
    provision(db, user.id)
    recovery_code = create_code(db, user.id)
    if invite:
        claim_invite(db, user.id, invite)
    emit(db, user.id, "registered:" + user.id, "fact.account.registered")
    db.commit()
    return {"id": user.id, "username": user.username, "recoveryCode": recovery_code}


@router.post("/auth/login")
def login(data: Credentials, request: Request, response: Response, db=Depends(get_db)):
    username = username_format(data.username)
    origin_check(request)
    rate_limit(db, "login-ip:" + request.client.host, 10, 900, increment=False)
    rate_limit(db, "login-user:" + username + request.client.host, 10, 900, increment=False)
    user = db.scalar(select(User).where(User.username == username, User.status == "active").with_for_update())
    if not user or not check_password(user, data.password):
        if not user:
            password_hasher.hash("constant-dummy-password")
        rate_limit(db, "login-ip:" + request.client.host, 10, 900)
        rate_limit(db, "login-user:" + username + request.client.host, 10, 900)
        raise Problem("LOGIN_FAILED", "用户名或密码不正确", 401)
    from .foundation.events import emit
    from .models import uid
    emit(db, user.id, "login:" + uid(), "fact.account.login")
    return issue_session(db, user, data.clientType, data.deviceName, response)


@router.post("/auth/refresh")
def refresh(data: Refresh, request: Request, response: Response, db=Depends(get_db)):
    import secrets
    origin_check(request)
    cookie = request.cookies.get("reader_refresh")
    if cookie and data.refreshToken:
        raise Problem("REFRESH_AMBIGUOUS", "刷新凭证来源冲突", 401)
    token = cookie or data.refreshToken
    if not token:
        raise Problem("AUTH_REQUIRED", "请登录", 401)
    hashed = digest(token)
    # Keep the same owner-before-session order used by account maintenance.
    # Bootstrap provisioning also locks the owner while repairing defaults.
    owner = db.scalar(select(DeviceSession.owner_id).where(DeviceSession.refresh_hash == hashed))
    if owner:
        db.scalar(select(User).where(User.id == owner).with_for_update())
    session = db.scalar(select(DeviceSession).where(DeviceSession.refresh_hash == hashed).with_for_update())
    if not session:
        # A replay revokes the full rotation chain.
        for previous in db.scalars(select(DeviceSession).where(DeviceSession.previous_hashes.contains([hashed]))):
            previous.revoked_at = now()
        db.commit()
        raise Problem("AUTH_REQUIRED", "刷新凭证已失效", 401)
    user = db.get(User, session.owner_id)
    if session.revoked_at or session.expires_at < now() or user.status != "active" or (bool(cookie) != (session.client_type == "web")):
        raise Problem("AUTH_REQUIRED", "登录已过期", 401)
    # Prepare everything before committing rotation: a failed startup must not
    # consume a refresh cookie that the client never gets a chance to receive.
    from .account_bootstrap import build
    bootstrap = build(db, user, session)
    fresh = secrets.token_urlsafe(48)
    session.previous_hashes = session.previous_hashes + [hashed]
    session.refresh_hash = digest(fresh)
    db.commit()
    return token_result(session, user, fresh, response, bootstrap=bootstrap)


@router.post("/auth/logout")
def logout(request: Request, response: Response, user=Depends(current_user), db=Depends(get_db)):
    request.state.session.revoked_at = now()
    response.delete_cookie("reader_refresh", path=settings.cookie_path_prefix+"/api/reader/v1/auth")
    db.commit()
    return {"revoked": True}


def settings_result(row):
    return {"version": row.version, "fontSize": row.font_size, "lineHeight": float(row.line_height), "theme": row.theme,
            "readingMode": row.reading_mode, "readingWidth": row.reading_width, "chapterLength": row.chapter_length}


@router.get("/auth/me")
def me(request: Request, user=Depends(current_user), db=Depends(get_db)):
    from .foundation.default_models import provision
    if provision(db, user.id):
        db.commit()
    return {"id": user.id, "username": user.username, "sessionId": request.state.session.id, "settings": settings_result(db.get(UserSettings, user.id)), "aiMode": settings.ai_mode}


@router.get("/settings")
def read_settings(user=Depends(current_user), db=Depends(get_db)):
    return settings_result(db.get(UserSettings, user.id))


@router.put("/settings")
def update_settings(data: ReadingSettings, user=Depends(current_user), db=Depends(get_db)):
    row = db.scalar(select(UserSettings).where(UserSettings.owner_id == user.id).with_for_update())
    if row.version != data.expectedVersion:
        raise Problem("SETTINGS_CONFLICT", "其他设备已更新阅读设置", 409, settings_result(row))
    row.version += 1
    row.font_size, row.line_height, row.theme = data.fontSize, str(data.lineHeight), data.theme
    for field, column in (("readingMode", "reading_mode"), ("readingWidth", "reading_width"), ("chapterLength", "chapter_length")):
        value = getattr(data, field)
        if field in data.model_fields_set and value is not None:
            setattr(row, column, value)
    db.commit()
    return settings_result(row)


class PasswordChange(Strict):
    currentPassword: str
    newPassword: str


class RecoveryReset(Strict):
    username: str = Field(min_length=3, max_length=32)
    recoveryCode: str = Field(max_length=100)
    newPassword: str = Field(min_length=12, max_length=128)


class RecoveryIssue(Strict):
    currentPassword: str = Field(max_length=128)


@router.post("/auth/recover")
def recover(data: RecoveryReset, request: Request, db=Depends(get_db)):
    origin_check(request)
    rate_limit(db, "recover-ip:" + request.client.host, 5, 900)
    username = username_format(data.username)
    password_format(data.newPassword)
    if not 20 <= len(data.recoveryCode.strip()) <= 100:
        raise Problem("RECOVERY_FAILED", "用户名或恢复码不正确，或恢复码已使用", 400)
    from .account_recovery import reset_password
    reset_password(db, username, data.recoveryCode, data.newPassword)
    return {"updated": True, "sessionsRevoked": True}


@router.post("/auth/recovery-code")
def recovery_code(data: RecoveryIssue, user=Depends(current_user), db=Depends(get_db)):
    from .foundation.core import lock_owner
    lock_owner(db, user.id)
    db.refresh(user)
    if not check_password(user, data.currentPassword):
        raise Problem("PASSWORD_INCORRECT", "当前密码不正确", 403)
    from .account_recovery import create_code
    code = create_code(db, user.id)
    db.commit()
    return {"recoveryCode": code}


@router.put("/auth/password")
def change_password(data: PasswordChange, request: Request, user=Depends(current_user), db=Depends(get_db)):
    from .foundation.core import lock_owner
    lock_owner(db, user.id)
    db.refresh(user)
    if not check_password(user, data.currentPassword):
        raise Problem("PASSWORD_INCORRECT", "当前密码不正确", 403)
    password_format(data.newPassword)
    user.password_hash = password_hasher.hash(data.newPassword)
    for session in db.scalars(select(DeviceSession).where(DeviceSession.owner_id == user.id, DeviceSession.id != request.state.session.id)):
        session.revoked_at = now()
    db.commit()
    return {"updated": True}


@router.get("/auth/sessions")
def sessions(request: Request, user=Depends(current_user), db=Depends(get_db)):
    return {"items": [{"id": s.id, "clientType": s.client_type, "deviceName": s.device_name, "createdAt": s.created_at.isoformat(), "current": s.id == request.state.session.id} for s in db.scalars(select(DeviceSession).where(DeviceSession.owner_id == user.id, DeviceSession.revoked_at.is_(None)).order_by(DeviceSession.created_at.desc()).limit(100))]}


@router.delete("/auth/sessions/{session_id}")
def revoke(session_id: str, user=Depends(current_user), db=Depends(get_db)):
    owned(db, DeviceSession, session_id, user.id).revoked_at = now()
    db.commit()
    return {"revoked": True}


class DeleteAccount(Strict):
    currentPassword: str
    confirmation: str


@router.post("/auth/delete-account", status_code=202)
def delete_account(data: DeleteAccount, response: Response, user=Depends(current_user), db=Depends(get_db)):
    from .models import CleanupReceipt, uid
    from .foundation.core import lock_owner
    lock_owner(db, user.id)
    db.refresh(user)
    if data.confirmation != "删除我的账号" or not check_password(user, data.currentPassword):
        raise Problem("DELETE_CONFIRMATION_REQUIRED", "请输入当前密码及“删除我的账号”", 403)
    user.status = "deleting"
    for session in db.scalars(select(DeviceSession).where(DeviceSession.owner_id == user.id)):
        session.revoked_at = now()
    for task in db.scalars(select(Task).where(Task.owner_id == user.id, Task.state.notin_(["succeeded", "failed", "cancelled"]))):
        task.cancel_requested = True
    task_id, receipt_id = uid(), uid()
    task = Task(id=task_id, owner_id=user.id, task_type="delete_account", payload={"receiptId": receipt_id})
    db.add(task)
    db.add(CleanupReceipt(id=receipt_id, task_id=task_id, deadline=now()+timedelta(hours=24)))
    db.commit()
    response.delete_cookie("reader_refresh", path=settings.cookie_path_prefix+"/api/reader/v1/auth")
    return {"taskId": task.id, "receiptId": receipt_id, "status": "deleting", "activeDataDeadlineHours": 24, "backupPolicy": "手工备份由部署者记录与销毁"}


@router.get("/auth/deletion-receipts/{receipt_id}")
def deletion_receipt(receipt_id: str, db=Depends(get_db)):
    from .models import CleanupReceipt
    receipt = db.get(CleanupReceipt, receipt_id)
    if not receipt:
        raise Problem("RESOURCE_NOT_FOUND", "回执不存在", 404)
    return {"status": receipt.state, "deadline": receipt.deadline.isoformat(), "completedAt": receipt.completed_at.isoformat() if receipt.completed_at else None}
