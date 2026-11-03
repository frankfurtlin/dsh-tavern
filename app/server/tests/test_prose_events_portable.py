"""Encrypted nonempty history with typed stable identities and event closure."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app import models as m
from app.prose.events import from_review,validate,fold
from app.portable.errors import ArchiveError
from test_portable_domain import domain,migrate_owned_database,seed_prose_history,restored,encrypted
from app.portable.package import prepare_backup
from app.portable.preflight import preflight_worlds


def seed_events(domain):
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        task=db.get(m.Task,history['task']);ledger={}
        for row in db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==history['batch']).order_by(m.GenerationChapterVersion.candidate_version,m.GenerationChapterVersion.chapter_index)):
            prior=[entry['id'] for value in ledger.get(str(row.candidate_version),{}).values() for entry in value['events']]
            quote=row.text.splitlines()[0]
            proposals=[{'kind':'item','subjectId':'local-0','certainty':'narrated','confidence':900,'statement':'字面local-0保留','quote':quote,'change':{'item':'原ID local-0 信封','fromHolderId':'local-0','toHolderId':'local-0','location':None}}]
            if row.chapter_index==2:
                proposals=[{**proposals[0],'kind':'knowledge','change':{'fact':'字面local-0保留','learnedByIds':['local-0']}}]
            if row.chapter_index==3:
                proposals=[{**proposals[0],'kind':'time','change':{'label':'前一夜','relation':'before','relativeTo':'c1:e1'}}]
            value=from_review({'text':json.dumps({'events':proposals},ensure_ascii=False),'finishReason':'stop'},row.text,row.chapter_index,{'local-0'},prior)
            assert value['status']=='model-proposed'
            ledger.setdefault(str(row.candidate_version),{})[str(row.chapter_index)]=value
        task.checkpoint={**task.checkpoint,'proseEventLedger':ledger};db.commit()
    return history,ledger


def test_all_versions_identity_fields_remap_but_literal_prose_and_event_ids_do_not(domain,tmp_path):
    history,ledger=seed_events(domain)
    archive=encrypted(domain,tmp_path)
    with archive.open('rb') as source,prepare_backup(source,'synthetic-test-password',tmp_path/'preview') as prepared:
        assert preflight_worlds(prepared)['domainObjectsChecked']>0
    result=restored(domain,tmp_path);ids=result['idMap'];actor=ids[('reader_entity',domain['branch']+':local-0')]
    with SessionLocal() as db:
        task=db.get(m.Task,ids[('task',history['task'])]);current=task.checkpoint['proseEventLedger']
        assert task.payload['importedHistory'] and task.state=='cancelled' and task.lease_owner is None
        for version,chapters in current.items():
            assert chapters['1']['events'][0]['subjectId']==actor!='local-0'
            assert chapters['1']['events'][0]['change']['fromHolderId']==actor
            assert chapters['2']['events'][0]['change']['learnedByIds']==[actor]
            assert chapters['3']['events'][0]['change']['relativeTo']=='c1:e1'
            assert chapters['1']['events'][0]['statement']=='字面local-0保留'
            assert chapters['1']['events'][0]['change']['item']=='原ID local-0 信封'
            fold([chapters[index] for index in sorted(chapters,key=int)])
        for row in db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==ids[('generation_batch',history['batch'])])):
            validate(current[str(row.candidate_version)][str(row.chapter_index)],row.text,subjects={actor})
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain['target']))==0
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id==domain['target']))==0
        assert db.get(m.Task,history['task']).checkpoint['proseEventLedger']==ledger


@pytest.mark.parametrize('fault',['body-hash','subject','holder','learned','prior','version','index'])
def test_event_poison_never_installs_target_rows(domain,tmp_path,fault):
    history,ledger=seed_events(domain);ledger=deepcopy(ledger)
    if fault=='body-hash':ledger['1']['1']['sourceHash']='0'*64
    elif fault=='subject':ledger['1']['1']['events'][0]['subjectId']='unlisted-person'
    elif fault=='holder':ledger['1']['1']['events'][0]['change']['toHolderId']='unlisted-person'
    elif fault=='learned':ledger['1']['2']['events'][0]['change']['learnedByIds']=['unlisted-person']
    elif fault=='prior':ledger['1']['3']['events'][0]['change']['relativeTo']='c2:e2'
    elif fault=='version':ledger['3']=deepcopy(ledger['1'])
    else:ledger['1']['3']['chapterIndex']=2
    with SessionLocal() as db:
        task=db.get(m.Task,history['task']);task.checkpoint={**task.checkpoint,'proseEventLedger':ledger};db.commit()
    with pytest.raises(ArchiveError,match='BACKUP_GENERATION_EVENTS_INVALID'):restored(domain,tmp_path)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain['target']))==0
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.owner_id==domain['target']))==0
