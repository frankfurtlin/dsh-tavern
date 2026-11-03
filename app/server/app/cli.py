"""Explicit operator setup for self-hosted installations; never auto-promote signup."""
import argparse
from sqlalchemy import select
from .config import settings
from .db import SessionLocal
from .models import User,UserSettings,ModelProfile,uid
from .foundation.core import account,audit,config


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['grant-admin','seed-demo'])
    parser.add_argument('--username',required=True,help='An existing account you registered yourself')
    args=parser.parse_args()
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.username==args.username.strip().lower(),User.status=='active').with_for_update())
        if not user:raise SystemExit('Account does not exist. Register in the web UI first; no changes made.')
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
