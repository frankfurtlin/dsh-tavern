"""Nonempty, actual encrypted PostgreSQL package round trip of quoted facts."""
from copy import deepcopy
from sqlalchemy import select,func
import pytest
from app.db import SessionLocal
from app import models as m
from app.prose.facts import from_review,validate_delta
from app.portable.errors import ArchiveError
from test_portable_domain import domain,migrate_owned_database,seed_prose_history,restored,encrypted
from app.portable.package import prepare_backup
from app.portable.preflight import preflight_worlds
import json

def seed_facts(domain):
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        task=db.get(m.Task,history['task']);ledger={}
        for row in db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==history['batch'])):
            quote=row.text.splitlines()[0]
            delta=from_review({'finishReason':'stop','text':json.dumps({'factDelta':[{'kind':'event','subjectId':None,'statement':'保留候选发生的事件','quote':quote}]},ensure_ascii=False)},row.text)
            ledger.setdefault(str(row.candidate_version),{})[str(row.chapter_index)]=delta
        task.checkpoint={**task.checkpoint,'proseFactLedger':ledger};db.commit()
    return history,ledger

def test_nonempty_encrypted_package_preserves_all_quoted_fact_versions_and_inert_tasks(domain,tmp_path):
    history,ledger=seed_facts(domain)
    archive=encrypted(domain,tmp_path)
    with archive.open('rb') as stream,prepare_backup(stream,'synthetic-test-password',tmp_path/'preview') as prepared:
        assert preflight_worlds(prepared)['domainObjectsChecked']>0
    result=restored(domain,tmp_path);ids=result['idMap']
    with SessionLocal() as db:
        task=db.get(m.Task,ids[('task',history['task'])]);assert task.checkpoint['proseFactLedger']==ledger
        assert task.payload['importedHistory'] is True and task.state=='cancelled' and task.lease_owner is None
        for row in db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==ids[('generation_batch',history['batch'])])):
            validate_delta(ledger[str(row.candidate_version)][str(row.chapter_index)],row.text)
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain['target']))==0

@pytest.mark.parametrize('fault',['body-hash','out-of-batch','nested-kind'])
def test_poisoned_fact_ledger_never_installs_target_rows(domain,tmp_path,fault):
    history,ledger=seed_facts(domain);ledger=deepcopy(ledger)
    if fault=='body-hash':ledger['1']['1']['sourceHash']='0'*64
    elif fault=='out-of-batch':ledger['1']['4']=deepcopy(ledger['1']['1'])
    else:ledger['1']['1']['entries'][0]['kind']={'unexpected':'object'}
    with SessionLocal() as db:
        task=db.get(m.Task,history['task']);task.checkpoint={**task.checkpoint,'proseFactLedger':ledger};db.commit()
    if fault!='nested-kind':
        archive=encrypted(domain,tmp_path)
        with archive.open('rb') as stream,prepare_backup(stream,'synthetic-test-password',tmp_path/'preview') as prepared:
            with pytest.raises(ArchiveError,match='BACKUP_GENERATION_FACTS_INVALID'):preflight_worlds(prepared)
    with pytest.raises(ArchiveError,match='BACKUP_GENERATION_FACTS_INVALID'):restored(domain,tmp_path)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain['target']))==0
        assert db.scalar(select(func.count()).select_from(m.GenerationBatch).where(m.GenerationBatch.owner_id==domain['target']))==0
