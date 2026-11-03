"""Real account/default-model transactions, synthetic providers and zero paid calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from types import SimpleNamespace
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app.db import SessionLocal
from app.models import ModelProfile, UserSettings, DeviceSession
from app.foundation.models import Account, Config, ModelGrant, RecoveryCode
from app.providers import decrypt_key
from app.foundation.default_models import provision
from app.errors import Problem

AUTH = '/api/reader/v1/auth'
PRODUCT = '/api/reader/v1/product'


def defaults(client, reader, profile):
    with SessionLocal() as db:
        db.add(Account(owner_id=reader['user']['id'], role='ADMIN'))
        db.commit()
    assert client.put('/api/world/v1/scene-profile', json={'key':'SYNTHETIC-IMAGE-KEY-NEVER-REAL', 'dailyLimit':0}).status_code == 200
    response = client.put(PRODUCT + '/admin/default-models', json={
        'requestKey':'enable-defaults-01', 'expectedVersion':0, 'textProfileId':profile['id'], 'imageEnabled':True})  # gitleaks:allow -- synthetic test fixture
    assert response.status_code == 200, response.text
    return response.json()


def test_registration_has_owned_defaults_but_no_platform_secret(client, reader, profile):
    defaults(client, reader, profile)
    registered = client.post(AUTH + '/register', json={'username':'new_player', 'password':'player-password-123'}).json()
    login = client.post(AUTH + '/login', json={'username':'new_player', 'password':'player-password-123', 'clientType':'android'}).json()
    client.headers['Authorization'] = 'Bearer ' + login['accessToken']
    models = client.get('/api/reader/v1/model-profiles').json()
    assert len(models['items']) == 1
    channel = models['items'][0]
    assert channel['source'] == 'platform' and channel['isDefault'] and channel['keyTail'] == ''
    assert channel['callsReserved'] == 0 and channel['id'] != profile['id']
    image = client.get('/api/world/v1/scene-profile').json()
    assert image['source'] == 'platform' and image['enabled'] and image['dailyLimit'] == 0
    assert 'SYNTHETIC' not in json.dumps([models, image, client.get(PRODUCT + '/public/config').json()])
    with SessionLocal() as db:
        copied = db.get(ModelProfile, channel['id'])
        assert copied.owner_id == registered['id'] and decrypt_key(copied.key_cipher) == 'TEST-KEY-NOT-REAL'
        recovery = db.get(RecoveryCode, registered['id'])
        assert registered['recoveryCode'] != recovery.code_hash
    denied = client.patch('/api/reader/v1/model-profiles/' + channel['id'], json={'expectedVersion':1, 'callBudget':0})
    assert denied.status_code == 403 and denied.json()['code'] == 'PLATFORM_PROFILE_READONLY'
    assert client.get(PRODUCT + '/admin/default-models').status_code == 403


def test_readiness_reports_platform_availability(client, reader, profile):
    assert client.get('/health/ready').json()['modelConfigured'] is False
    defaults(client, reader, profile)
    ready = client.get('/health/ready').json()
    assert ready['modelConfigured'] is True and 'key_cipher' not in json.dumps(ready)


def test_defaults_retry_conflict_and_no_overwrite(client, reader, profile):
    configured = defaults(client, reader, profile)
    assert configured['version'] == 1
    body = {'requestKey':'enable-defaults-01', 'expectedVersion':0, 'textProfileId':profile['id'], 'imageEnabled':True}  # gitleaks:allow -- synthetic test fixture
    assert client.put(PRODUCT + '/admin/default-models', json=body).json()['version'] == 1
    body['requestKey'] = 'stale-settings-02'
    assert client.put(PRODUCT + '/admin/default-models', json=body).status_code == 409
    owner = reader['user']['id']
    def apply():
        with SessionLocal() as db:
            provision(db, owner)
            db.commit()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: apply(), range(4)))
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ModelProfile).where(ModelProfile.owner_id == owner)) == 1
        assert db.get(ModelProfile, profile['id']).name == 'fixture-only'
        assert db.get(UserSettings, owner).default_profile_id == profile['id']


def test_concurrent_provisioning_copies_only_once(client, reader, profile):
    defaults(client, reader, profile)
    with SessionLocal() as db:
        from app.models import User
        from app.security import password_hasher
        user = User(username='legacy_player', password_hash=password_hasher.hash('player-password-123'))
        db.add(user);db.flush();owner = user.id
        db.add(UserSettings(owner_id=owner));db.commit()
    def apply(_):
        with SessionLocal() as db:
            provision(db, owner);db.commit()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(apply, range(4)))
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ModelProfile).where(ModelProfile.owner_id == owner)) == 1
        grant = db.get(ModelGrant, owner)
        assert grant.text_profile_id and grant.image_profile_id


def test_recovery_consumed_once_and_revokes_every_session(client, reader):
    register = client.post(AUTH + '/register', json={'username':'recover_player','password':'old-password-1234'}).json()
    login = client.post(AUTH + '/login', json={'username':'recover_player','password':'old-password-1234','clientType':'android'}).json()
    body = {'username':'recover_player','recoveryCode':register['recoveryCode'],'newPassword':'new-password-5678'}
    assert client.post(AUTH + '/recover', json={**body,'recoveryCode':'invalid-recovery-code-000'}).status_code == 400
    assert client.post(AUTH + '/recover', json=body).status_code == 200
    assert client.post(AUTH + '/recover', json=body).status_code == 400
    with TestClient(app) as other:
        assert other.get(AUTH + '/me', headers={'Authorization':'Bearer ' + login['accessToken']}).status_code == 401
    assert client.post(AUTH + '/login', json={'username':'recover_player','password':'old-password-1234'}).status_code == 401
    assert client.post(AUTH + '/login', json={'username':'recover_player','password':'new-password-5678'}).status_code == 200
    with SessionLocal() as db:
        code = db.get(RecoveryCode, register['id'])
        assert code.code_hash is None and code.used_at is not None


def test_recovery_requires_password_and_rotation_invalidates_old_code(client, reader):
    assert client.post(AUTH + '/recovery-code', json={'currentPassword':'wrong-password-123'}).status_code == 403
    first = client.post(AUTH + '/recovery-code', json={'currentPassword':'reader-password-123'}).json()['recoveryCode']
    second = client.post(AUTH + '/recovery-code', json={'currentPassword':'reader-password-123'}).json()['recoveryCode']
    assert first != second
    assert client.post(AUTH + '/recover', json={'username':'reader_one','recoveryCode':first,'newPassword':'replacement-password-123'}).status_code == 400
    assert client.post(AUTH + '/recover', json={'username':'reader_one','recoveryCode':second,'newPassword':'replacement-password-123'}).status_code == 200


def test_recovery_does_not_reset_quota_or_private_data(client, reader, profile):
    with SessionLocal() as db:
        row = db.get(ModelProfile, profile['id']);row.calls_reserved = 37;db.commit()
    code = client.post(AUTH + '/recovery-code', json={'currentPassword':'reader-password-123'}).json()['recoveryCode']
    assert client.post(AUTH + '/recover', json={'username':'reader_one','recoveryCode':code,'newPassword':'replacement-password-123'}).status_code == 200
    with SessionLocal() as db:
        assert db.get(ModelProfile, profile['id']).calls_reserved == 37
        assert db.get(UserSettings, reader['user']['id']) is not None


@pytest.mark.parametrize('operation',['password','deletion'])
def test_inflight_old_password_cannot_overwrite_recovery(client, reader, operation):
    from app.models import User
    from app.api_auth import change_password, delete_account, PasswordChange, DeleteAccount
    code=client.post(AUTH+'/recovery-code',json={'currentPassword':'reader-password-123'}).json()['recoveryCode']
    with SessionLocal() as old_request:
        # The request authenticated before recovery, retaining an old ORM value.
        stale_user=old_request.get(User,reader['user']['id'])
        assert client.post(AUTH+'/recover',json={'username':'reader_one','recoveryCode':code,'newPassword':'recovered-password-123'}).status_code==200
        with pytest.raises(Problem) as denied:
            if operation=='password':
                change_password(PasswordChange(currentPassword='reader-password-123',newPassword='stale-password-123'),
                    SimpleNamespace(state=SimpleNamespace(session=SimpleNamespace(id=reader['sessionId']))),stale_user,old_request)
            else:
                delete_account(DeleteAccount(currentPassword='reader-password-123',confirmation='删除我的账号'),Response(),stale_user,old_request)
        assert denied.value.code in ('PASSWORD_INCORRECT','DELETE_CONFIRMATION_REQUIRED')
        old_request.rollback()
    assert client.post(AUTH+'/login',json={'username':'reader_one','password':'recovered-password-123'}).status_code==200
