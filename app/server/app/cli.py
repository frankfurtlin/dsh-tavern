"""Explicit operator setup for self-hosted installations; never auto-promote signup."""
import argparse
import secrets
from sqlalchemy import select
from .config import settings
from .db import SessionLocal
from .models import User,UserSettings,ModelProfile,uid
from .foundation.core import account,audit,config


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['grant-admin','seed-demo','configure-dsh-model'])
    parser.add_argument('--username',required=True,help='An existing account you registered yourself')
    args=parser.parse_args()
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.username==args.username.strip().lower(),User.status=='active').with_for_update())
        if not user:raise SystemExit('Account does not exist. Register in the web UI first; no changes made.')
        if args.command=='configure-dsh-model':
            # The gateway ignores base_url/key_cipher; the profile exists so the
            # existing budget, selection and display paths keep working.
            from .providers import encrypt_key
            key=secrets.token_urlsafe(32)
            profile=db.scalar(select(ModelProfile).where(ModelProfile.owner_id==user.id,ModelProfile.base_url=='dsh://agent').order_by(ModelProfile.created_at,ModelProfile.id).with_for_update())
            if profile:
                profile.name='DSH 模型';profile.model_id='dsh-default';profile.key_cipher=encrypt_key(key);profile.key_tail=key[-4:]
                profile.context_window=128000;profile.output_parameter='max_tokens';profile.supports_json=True;profile.status='active'
            else:
                profile=ModelProfile(owner_id=user.id,name='DSH 模型',base_url='dsh://agent',model_id='dsh-default',
                    key_cipher=encrypt_key(key),key_tail=key[-4:],context_window=128000,output_parameter='max_tokens',supports_json=True)
                db.add(profile);db.flush()
            preferences=db.get(UserSettings,user.id)
            if not preferences:raise SystemExit('User settings are missing; open the app once, then retry.')
            preferences.default_profile_id=profile.id
            db.commit();print(profile.id);return
        if args.command=='grant-admin':
            account(db,user).role='ADMIN'
            audit(db,user.id,'operator.bootstrap',user.id,'bootstrap:'+uid(),{'source':'explicit operator CLI'})
            db.commit();print('ADMIN granted to the explicitly named account.');return
        if account(db,user).role!='ADMIN':raise SystemExit('seed-demo requires an explicitly granted ADMIN account')
        if settings.ai_mode=='mock':
            from .providers import encrypt_key
            from .foundation.default_models import configure
            from .foundation.schemas import DefaultModelsInput
            profile=db.scalar(select(ModelProfile).where(ModelProfile.owner_id==user.id,ModelProfile.base_url=='mock://deterministic'))
            if not profile:
                profile=ModelProfile(owner_id=user.id,name='演示模型 · 确定性模拟',base_url='mock://deterministic',model_id='demo-v1',
                    key_cipher=encrypt_key('mock-only-not-a-provider-key'),key_tail='demo',context_window=32768,
                    call_budget=0,input_budget=10000000,output_budget=1000000)
                db.add(profile);db.flush()
            preferences=db.get(UserSettings,user.id);preferences.default_profile_id=profile.id
            configure(db,user,DefaultModelsInput(expectedVersion=config(db,'platform-models').get('version',0),
                textProfileId=profile.id,imageEnabled=False,requestKey='demo:'+uid()))
        db.commit()
    # Reuse the same idempotent publication path as the running product.
    from starlette.requests import Request
    from .experience.curated import publish_curated
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.username==args.username.strip().lower()))
        result=publish_curated(Request({'type':'http','method':'POST','headers':[(b'idempotency-key',('seed-demo:'+uid()).encode())]}),user,db)
        print('Published sample library:',result['published'])


if __name__=='__main__':main()
