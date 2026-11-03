"""Startup transactions and beta defaults; no provider calls."""
import json
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import ModelProfile,UserSettings
from app.foundation.models import Config,VisionSettings,ModelGrant
from app.foundation.vision import FIELDS
from app.foundation.default_models import provision
from test_account_onboarding import defaults

AUTH='/api/reader/v1/auth'


def test_refresh_bootstrap_replaces_me_and_world_config_without_exposing_keys(client,reader,profile):
    defaults(client,reader,profile)
    response=client.post(AUTH+'/refresh',json={'refreshToken':reader['refreshToken']})
    assert response.status_code==200,response.text
    value=response.json();boot=value['bootstrap']
    assert boot['user']['id']==reader['user']['id'] and boot['user']['sessionId']==value['sessionId']
    assert boot['world']==client.get('/api/world/v1/config').json()
    assert boot['user']==client.get(AUTH+'/me').json()
    assert boot['defaultModels']['text']['enabled'] and boot['defaultModels']['image']['enabled']
    assert not any(secret in json.dumps(boot) for secret in ['key_cipher','SYNTHETIC','TEST-KEY-NOT-REAL','accessToken','refreshToken','password_hash'])


def test_failed_bootstrap_does_not_consume_refresh_or_commit_partial_provisioning(client,reader,monkeypatch):
    import app.account_bootstrap as startup
    original=startup.build
    def fail(*args):raise RuntimeError('synthetic startup fault')
    monkeypatch.setattr(startup,'build',fail)
    assert client.post(AUTH+'/refresh',json={'refreshToken':reader['refreshToken']}).status_code==503
    monkeypatch.setattr(startup,'build',original)
    assert client.post(AUTH+'/refresh',json={'refreshToken':reader['refreshToken']}).status_code==200


def test_visual_only_legacy_account_receives_text_default_once_without_resetting_usage(client,reader,profile):
    defaults(client,reader,profile)
    with SessionLocal() as db:
        p=db.get(ModelProfile,profile['id']);policy={k:getattr(p,k) for k in FIELDS}
        db.add(Config(key='platform-vision',value=policy));db.commit()
    registered=client.post(AUTH+'/register',json={'username':'visual_legacy','password':'legacy-password-123'}).json()
    owner=registered['id']
    with SessionLocal() as db:
        grant=db.get(ModelGrant,owner);db.delete(db.get(ModelProfile,grant.text_profile_id));grant.text_profile_id=None
        db.get(UserSettings,owner).default_profile_id=None
        visual=db.get(ModelProfile,db.get(VisionSettings,owner).platform_profile_id);visual.calls_reserved=7;db.commit()
    login=client.post(AUTH+'/login',json={'username':'visual_legacy','password':'legacy-password-123','clientType':'android'}).json()
    assert login['bootstrap']['defaultModels']['text']['enabled']
    with SessionLocal() as db:
        grant=db.get(ModelGrant,owner);selected=db.get(UserSettings,owner).default_profile_id
        assert selected==grant.text_profile_id and selected!=visual.id
        assert db.get(ModelProfile,visual.id).calls_reserved==7
        assert not provision(db,owner)
        assert db.scalar(select(func.count()).select_from(ModelProfile).where(ModelProfile.owner_id==owner))==2


def test_beta_falls_back_to_owned_platform_vision_when_selection_missing(client,reader,profile):
    with SessionLocal() as db:
        p=db.get(ModelProfile,profile['id']);db.add(Config(key='platform-vision',value={k:getattr(p,k) for k in FIELDS}));db.commit()
        provision(db,reader['user']['id']);db.commit()
        row=db.get(VisionSettings,reader['user']['id']);row.profile_id=None;row.managed=False;db.commit()
        assert provision(db,reader['user']['id']);db.commit()
        assert row.profile_id==row.platform_profile_id and row.managed
