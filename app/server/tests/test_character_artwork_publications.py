"""Publish freezes selected art, copy is private, withdrawal leaves existing copies usable."""
from sqlalchemy import select
from app.db import SessionLocal
from app.world.models import World
from app.scenes.models import CharacterArtwork,CharacterLook
from app.scenes.artworks import create_pair
from .test_character_artworks import upload_body
from .test_authoring_publications import draft,submit,moderator,approve,other,post,login,API,WORLD,isolated_storage_and_mock_only

def test_published_artwork_is_frozen_copied_and_independent(client,reader):
    world,current=draft(client);entity=next(e['id'] for e in current['value']['definition']['entities'] if e['type']=='character')
    upload=client.post(WORLD+'/worlds/'+world+'/character-looks/upload',json=upload_body(entity),headers={'Idempotency-Key':'published-face'});assert upload.status_code==201,upload.text
    art=upload.json();publication=submit(client,world)
    # A later personal crop cannot silently replace the version under review.
    body=upload_body(entity,art['version']);body['crop']={'x':0,'y':0,'width':200,'height':200}
    changed=client.post(WORLD+'/worlds/'+world+'/character-looks/upload',json=body,headers={'Idempotency-Key':'new-personal-face'});assert changed.status_code==201
    moderator(client);review=approve(client,publication);other(client)
    detail=client.get(API+'/publications/'+publication['id']).json();person=next(e for e in detail['characters'] if e['id']==entity)
    expected=None
    with SessionLocal() as db:expected=db.get(CharacterArtwork,art['selectedId']).avatar_image
    image=client.get(person['avatarUrl']);assert image.status_code==200 and image.content==expected
    copied=post(client,API+'/publications/'+publication['id']+'/copy',{},'copy-with-default')['worldId']
    entry=next(e for e in client.get(WORLD+'/worlds/'+copied+'/character-looks').json()['characters'] if e['entityId']==entity)
    assert entry['selectedId']!=art['selectedId']
    owned_url=next(v for v in entry['versions'] if v['id']==entry['selectedId'])['avatarUrl']
    assert client.get(owned_url).content==expected
    assert client.get(art['artwork']['avatarUrl']).status_code==404
    login(client,'reader_one');withdraw=post(client,API+'/publications/'+publication['id']+'/withdraw',{'expectedVersion':review['version']},'withdraw-with-art')
    assert client.get(person['avatarUrl']).status_code==404
    login(client,'public_reader');assert client.get(owned_url).status_code==200
