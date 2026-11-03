"""Nonempty authenticated AES transport for owned selected-writing history."""
from pathlib import Path
from copy import deepcopy
import hashlib
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal,engine
from app import models as m
from app.portable.package import write_backup,prepare_backup
from app.portable.snapshot import build_domain_snapshot
from app.portable.restore import restore_domain
from app.portable.errors import ArchiveError
from app.portable.models import PortableBase,PORTABLE_TABLES
from app.assets.models import AssetsBase,ASSET_TABLES
from .test_selection_generations import finite_selected_provider,request,apply,BODY,process
from .helpers import BASE

@pytest.fixture(autouse=True)
def migration():
    assert engine.url.database=='selection_generation_test'
    PortableBase.metadata.create_all(engine,tables=PORTABLE_TABLES)
    AssetsBase.metadata.create_all(engine,tables=ASSET_TABLES)

def seed(client,reader,demo,profile,purpose):
    receipt,body,original=request(client,reader,demo,profile,purpose)
    process(receipt['taskId'])
    accepted=apply(client,receipt,body);assert accepted.status_code==201,accepted.text
    response=client.post(BASE+'/auth/register',json={'username':'selection_backup_target','password':'synthetic-target-password'})
    assert response.status_code==201,response.text
    with SessionLocal() as db:
        target=db.scalar(select(m.User).where(m.User.username=='selection_backup_target')).id
        owner=db.scalar(select(m.Book).where(m.Book.id==demo['bookId'])).owner_id
    return receipt,accepted.json(),owner,target,original

def encrypted(demo,owner,path):
    output=path/'owned-selection-generation.srbackup'
    with SessionLocal() as db:
        with build_domain_snapshot(db,owner,'book-'+demo['bookId'],path/'snapshot') as archive:
            with output.open('wb') as stream:write_backup(archive.root,archive.manifest,stream,'own-selected-generation-aes',path/'encryption')
    return output

@pytest.mark.parametrize('purpose',['expand','rewrite'])
def test_aes_retains_exact_ai_selection_origins_and_inactive_transport(client,reader,demo,profile,finite_selected_provider,tmp_path,purpose):
    receipt,accepted,owner,target,original=seed(client,reader,demo,profile,purpose)
    package=encrypted(demo,owner,tmp_path);before=hashlib.sha256(package.read_bytes()).hexdigest()
    with package.open('rb') as stream,prepare_backup(stream,'own-selected-generation-aes',tmp_path/'prepared') as prepared:
        with SessionLocal() as db:restored=restore_domain(db,prepared,target)
    assert hashlib.sha256(package.read_bytes()).hexdigest()==before
    mapping=restored['idMap']
    with SessionLocal() as db:
        revision=db.get(m.Revision,mapping['revision',accepted['revisionId']]);batch=db.get(m.GenerationBatch,mapping['generation_batch',receipt['generationId']])
        proof=revision.provenance['selectionEdit'];origin=revision.provenance['generationSource']
        assert proof['replacementText']==(original+'\n\n'+BODY if purpose=='expand' else BODY)
        assert origin['generationId']==batch.id
        row=db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==batch.id))
        assert origin['contentId']==row.content_id and row.text==BODY
        assert proof['startAnchor']==batch.request['selection']['startAnchor']
        assert proof['endAnchor']==batch.request['selection']['endAnchor']
        task=db.get(m.Task,batch.task_id);assert task.checkpoint['portableHistoricalCurrent']['state']=='succeeded'
        assert task.state!='queued' and task.lease_owner is None
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id==target))==0
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==target))==0
        assert len(finite_selected_provider)==1

@pytest.mark.parametrize('invalid_version',[999,True,1.0,'1',None,0,-1])
def test_reauthenticated_wrong_candidate_version_creates_no_owned_objects(client,reader,demo,profile,finite_selected_provider,tmp_path,invalid_version):
    receipt,accepted,owner,target,_=seed(client,reader,demo,profile,'rewrite')
    with SessionLocal() as db:
        from sqlalchemy.orm.attributes import flag_modified
        row=db.get(m.Revision,accepted['revisionId']);prov=deepcopy(row.provenance);prov['generationSource']['candidateVersion']=invalid_version;row.provenance=prov;flag_modified(row,'provenance');db.commit()
    package=encrypted(demo,owner,tmp_path)
    with package.open('rb') as stream,prepare_backup(stream,'own-selected-generation-aes',tmp_path/'prepared') as prepared:
        with SessionLocal() as db:
            with pytest.raises(ArchiveError) as refused:restore_domain(db,prepared,target)
            assert refused.value.code=='BACKUP_GENERATION_SELECTION_INVALID'
    with SessionLocal() as db:
        for cls in (m.Book,m.Branch,m.Revision,m.ContentBlock,m.Task,m.GenerationBatch,m.GenerationChapterVersion,m.ModelProfile,m.UsageRecord):
            assert db.scalar(select(func.count()).select_from(cls).where(cls.owner_id==target))==0
