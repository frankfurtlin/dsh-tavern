"""Nonempty actual encrypted backup: original bytes, projection and all versions."""
from copy import deepcopy
import json
from sqlalchemy import select, func
import pytest
from app.db import SessionLocal
from app import models as m
from app.prose.presentation import record, validate_record
from app.domain import canonical, digest
from app.storage import _write_task_journal
from app.portable.errors import ArchiveError
from app.portable.package import prepare_backup
from app.portable.preflight import preflight_worlds
from test_portable_domain import domain, migrate_owned_database, seed_prose_history, restored, encrypted


def seed_presentations(domain):
    history = seed_prose_history(domain)
    with SessionLocal() as db:
        task = db.get(m.Task, history['task']); ledger = {}
        for row in db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id == history['batch'])):
            raw = '<think>合成分析，仅作原返回</think>' + json.dumps({'title':row.title,'body':row.text,'extension':{'key':'用户字面字段','bookId':domain['book']}},ensure_ascii=False)
            value = record(raw,row.chapter_index,finish_reason='stop' if row.completeness == 'complete' else None)
            if row.chapter_index == 2:
                original = record('{"tool_call":"无可读正文"}',row.chapter_index,finish_reason='stop')
                value = record(raw,row.chapter_index,kind='repair',finish_reason='stop' if row.completeness == 'complete' else None,previous=original)
            ledger.setdefault(str(row.candidate_version),{})[str(row.chapter_index)] = value
        task.checkpoint = {**task.checkpoint,'prosePresentationLedger':ledger}; db.commit()
    return history, ledger


def test_all_versions_and_both_attempts_restore_exactly_without_credential_or_provider(domain,tmp_path):
    history, ledger = seed_presentations(domain)
    archive = encrypted(domain,tmp_path)
    with archive.open('rb') as stream, prepare_backup(stream,'synthetic-test-password',tmp_path/'preview') as prepared:
        assert preflight_worlds(prepared)['domainObjectsChecked'] > 0
    result = restored(domain,tmp_path); ids = result['idMap']
    with SessionLocal() as db:
        task = db.get(m.Task,ids[('task',history['task'])])
        assert task.checkpoint['prosePresentationLedger'] == ledger
        assert task.payload['importedHistory'] and task.state == 'cancelled' and task.lease_owner is None
        for row in db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id == ids[('generation_batch',history['batch'])])):
            value = ledger[str(row.candidate_version)][str(row.chapter_index)]
            assert validate_record(value,row.text,row.title) == value
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id == domain['target'])) == 0
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id == domain['target'])) == 0


def test_private_received_bytes_precede_failed_mirror_and_enter_backup_without_changing_source_rows(domain,tmp_path):
    from app.prose.service import stream_name
    history, ledger = seed_presentations(domain)
    with SessionLocal() as db:
        row = db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id == history['batch'],m.GenerationChapterVersion.candidate_version == 2,m.GenerationChapterVersion.chapter_index == 3))
        before = (row.text,row.title,row.snapshot_version,row.stream_version,row.content_hash,row.completeness,row.durability)
        task = db.get(m.Task,history['task']); old_checkpoint = deepcopy(task.checkpoint)
        raw = '<think>原返回分析</think>{"title":"未完的新片段","body":"新收到🙂内容'
        received = record(raw,3); body = received['presentation']
        value = {'text':body['text'],'title':body['title'],'contentHash':digest(body['text']),
                 'snapshotVersion':row.snapshot_version+1,'streamVersion':row.stream_version+1,
                 'completeness':'partial','hints':[],'final':False,'rawPresentation':received}
        envelope = {'taskId':task.id,'generationId':history['batch'],'candidateVersion':2,'chapterIndex':3,'contentId':row.content_id,'value':value,'hash':digest(canonical(value))}
    _write_task_journal(domain['owner'],history['task'],stream_name(history['task'],2,3),canonical(envelope).encode())
    result = restored(domain,tmp_path); ids = result['idMap']
    with SessionLocal() as db:
        source = db.get(m.GenerationChapterVersion,row.id)
        assert (source.text,source.title,source.snapshot_version,source.stream_version,source.content_hash,source.completeness,source.durability) == before
        assert db.get(m.Task,history['task']).checkpoint == old_checkpoint
        target = db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id == ids[('generation_batch',history['batch'])],m.GenerationChapterVersion.candidate_version == 2,m.GenerationChapterVersion.chapter_index == 3))
        assert (target.text,target.title,target.snapshot_version,target.stream_version) == (body['text'],body['title'],value['snapshotVersion'],value['streamVersion'])
        assert target.completeness == 'partial' and target.durability == 'unconfirmed'
        assert db.get(m.Task,ids[('task',history['task'])]).checkpoint['prosePresentationLedger']['2']['3'] == received


@pytest.mark.parametrize('fault',['raw-hash','projection-body','row-body','row-title','version','index','fake-complete','extra-field'])
def test_poisoned_received_history_is_rejected_without_target_rows(domain,tmp_path,fault):
    history, ledger = seed_presentations(domain); ledger = deepcopy(ledger)
    with SessionLocal() as db:
        row = db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id == history['batch'],m.GenerationChapterVersion.candidate_version == 2,m.GenerationChapterVersion.chapter_index == 3))
        value = ledger['2']['3']
        if fault == 'raw-hash': value['attempts'][0]['rawHash'] = '0'*64
        elif fault == 'projection-body': value['presentation']['text'] += '改'
        elif fault == 'row-body': row.text += '改'; row.content_hash = digest(row.text)
        elif fault == 'row-title': row.title += '改'
        elif fault == 'version': ledger['3'] = {'3':value}
        elif fault == 'index': value['chapterIndex'] = 2
        elif fault == 'fake-complete': row.completeness = 'complete'
        else: value['attempts'][0]['authorization'] = '不是供应商授权'
        task = db.get(m.Task,history['task']); task.checkpoint = {**task.checkpoint,'prosePresentationLedger':ledger}; db.commit()
    with pytest.raises(ArchiveError, match='BACKUP_GENERATION_PRESENTATION_INVALID'):
        restored(domain,tmp_path)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id == domain['target'])) == 0
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.owner_id == domain['target'])) == 0
