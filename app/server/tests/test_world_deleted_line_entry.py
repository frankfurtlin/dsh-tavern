"""Only an owned deleted line in its own world authorizes entry fallback."""
from uuid import uuid4
from app.db import SessionLocal
from app.world.models import World, WorldLine
from .test_world import API, start, fast_fixture_stream
from .test_world_lines import delete, fork


def test_deleted_own_line_distinguishes_its_tombstone_without_exposing_foreign_ids(client, reader, demo, profile):
    world,line=start(client,reader,demo,profile)
    child=fork(client,world,line)
    delete(client,world,line)
    path=f"/worlds/{world['id']}/lines/{line['id']}"
    for suffix in ['', '/history', '/drafts']:
        response=client.get(API+path+suffix)
        assert response.status_code==404
        assert response.json()['code']=='WORLD_LINE_DELETED'
    assert client.get(API+f"/worlds/{world['id']}").json()['activeLineId']==child['id']
    missing=client.get(API+f"/worlds/{world['id']}/lines/{uuid4().hex}")
    assert missing.status_code==404 and missing.json()['code']=='RESOURCE_NOT_FOUND'
    with SessionLocal() as db:
        original=db.get(World,world['id'])
        other=World(id=uuid4().hex,owner_id=reader['user']['id'],name='独立合成世界',source_book_id=original.source_book_id,source_title=original.source_title,source=original.source,definition=original.definition,status='active')
        db.add(other);db.commit();other_id=other.id
    wrong_world=client.get(API+f"/worlds/{other_id}/lines/{line['id']}")
    assert wrong_world.status_code==404 and wrong_world.json()['code']=='RESOURCE_NOT_FOUND'
    # A tombstone from a different owner remains indistinguishable from missing.
    with SessionLocal() as db:
        # The User FK requires a real synthetic owner; create it without signing in.
        from app.models import User
        owner=uuid4().hex
        db.add(User(id=owner,username='other_synthetic_owner',password_hash='not-a-login-password'))
        db.flush()
        db.get(WorldLine,line['id']).owner_id=owner
        db.commit()
    foreign=client.get(API+path)
    assert foreign.status_code==404 and foreign.json()['code']=='RESOURCE_NOT_FOUND'
