"""Explicit operator setup for self-hosted installations; never auto-promote signup."""
import argparse
import secrets

from sqlalchemy import select

from .db import SessionLocal
from .models import ModelProfile, User, UserSettings, uid  # noqa: F401


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['configure-dsh-model'])
    parser.add_argument('--username', required=True, help='An existing account you registered yourself')
    args = parser.parse_args()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.username.strip().lower(),
                                           User.status == 'active').with_for_update())
        if not user:
            raise SystemExit('Account does not exist. Register in the web UI first; no changes made.')
        from .providers import encrypt_key
        key = secrets.token_urlsafe(32)
        profile = db.scalar(select(ModelProfile).where(
            ModelProfile.owner_id == user.id, ModelProfile.base_url == 'dsh://agent'
        ).order_by(ModelProfile.created_at, ModelProfile.id).with_for_update())
        if profile:
            profile.name = 'DSH 模型'; profile.model_id = 'dsh-default'
            profile.key_cipher = encrypt_key(key); profile.key_tail = key[-4:]
            profile.context_window = 128000; profile.output_parameter = 'max_tokens'
            profile.supports_json = True; profile.status = 'active'
        else:
            profile = ModelProfile(owner_id=user.id, name='DSH 模型', base_url='dsh://agent',
                model_id='dsh-default', key_cipher=encrypt_key(key), key_tail=key[-4:],
                context_window=128000, output_parameter='max_tokens', supports_json=True)
            db.add(profile); db.flush()
        preferences = db.get(UserSettings, user.id)
        if not preferences:
            raise SystemExit('User settings are missing; open the app once, then retry.')
        preferences.default_profile_id = profile.id
        db.commit(); print(profile.id)


if __name__ == '__main__':
    main()
