"""Completed paired images survive encrypted cross-account restore without model calls."""
from copy import deepcopy
import io
import pytest
from PIL import Image
from sqlalchemy import select
from app.db import SessionLocal
from app.world.models import World,WorldCheckpoint
from app.scenes.models import CharacterArtwork,CharacterLook
from app.scenes.artworks import create_pair
from app.portable.package import prepare_backup
from app.portable.preflight import preflight_worlds
from app.portable.errors import ArchiveError
from .test_portable_domain import domain,migrate_owned_database,restored,encrypted

def seed(domain):
    with SessionLocal() as db:
        world=db.get(World,domain['world']);entity=next(e['id'] for e in world.definition['entities'] if e['type']=='character')
        output=io.BytesIO();Image.new('RGB',(180,300),'#496a82').save(output,format='PNG')
        row=create_pair(db,domain['owner'],world,entity,output.getvalue(),{'x':30,'y':10,'width':120,'height':120},'user_upload','Synthetic owned illustration',{'name':'角色'})
        db.add(CharacterLook(owner_id=domain['owner'],world_id=world.id,entity_id=entity,selected_artwork_id=row.id,version=2));db.commit()
        return {'id':row.id,'image':deepcopy(row.image),'full':row.full_image,'avatar':row.avatar_image,'entity':entity,
            'state':deepcopy(db.get(WorldCheckpoint,domain['checkpoint']).state)}

def test_character_artwork_encrypted_restore_and_reexport_are_role_scoped_and_canon_neutral(domain,tmp_path):
    source=seed(domain);receipt=restored(domain,tmp_path,world=True);ids=receipt['idMap']
    with SessionLocal() as db:
        row=db.get(CharacterArtwork,ids[('character_artwork',source['id'])]);world_id=ids[('world',domain['world'])]
        assert row.owner_id==domain['target'] and row.world_id==world_id and row.entity_id!=source['entity']
        assert row.full_image==source['full'] and row.avatar_image==source['avatar'] and row.image==source['image']
        assert row.descriptor['entityId']==row.entity_id
        choice=db.scalar(select(CharacterLook).where(CharacterLook.world_id==world_id));assert choice.selected_artwork_id==row.id and choice.version==2
        assert db.get(WorldCheckpoint,domain['checkpoint']).state==source['state']
    copied={**domain,'owner':domain['target'],'target':domain['stranger'],'world':world_id}
    again=restored(copied,tmp_path/'again',world=True)
    with SessionLocal() as db:assert db.get(CharacterArtwork,again['idMap'][('character_artwork',row.id)]).avatar_image==source['avatar']

def test_character_artwork_preflight_rejects_cross_role_selection(domain,tmp_path):
    seed(domain)
    with SessionLocal() as db:
        choice=db.scalar(select(CharacterLook).where(CharacterLook.world_id==domain['world']));choice.entity_id='invented-cross-role';db.commit()
    with pytest.raises(ArchiveError):
        raw=encrypted(domain,tmp_path,world=True)
        with raw.open('rb') as stream,prepare_backup(stream,'synthetic-test-password',tmp_path/'checked') as prepared:preflight_worlds(prepared)


def test_artwork_of_removed_unplayed_author_role_does_not_block_remaining_world_backup(domain,tmp_path):
    source=seed(domain)
    with SessionLocal() as db:
        world=db.get(World,domain['world']);art=db.get(CharacterArtwork,source['id'])
        orphan=create_pair(db,domain['owner'],world,'removed-unplayed-role',art.full_image,None,'user_upload','Synthetic owned illustration',{'name':'已移除的角色'})
        db.add(CharacterLook(owner_id=domain['owner'],world_id=world.id,entity_id=orphan.entity_id,selected_artwork_id=orphan.id));db.commit();orphan_id=orphan.id
    receipt=restored(domain,tmp_path,world=True)
    assert ('character_artwork',source['id']) in receipt['idMap'] and ('character_artwork',orphan_id) not in receipt['idMap']


def test_paired_artwork_survives_export_with_detached_legacy_published_reading_family(domain,tmp_path):
    from .test_portable_domain import seed_legacy_publication
    source=seed(domain);seed_legacy_publication(domain)
    receipt=restored(domain,tmp_path,world=True)
    with SessionLocal() as db:
        art=db.get(CharacterArtwork,receipt['idMap'][('character_artwork',source['id'])])
        assert art.full_image==source['full'] and art.avatar_image==source['avatar'] and art.image==source['image']
