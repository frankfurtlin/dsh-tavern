"""Nonempty encrypted copy remaps typed bindings and rejects broken source maps."""
from copy import deepcopy
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app import models as m
from app.portable.errors import ArchiveError
from app.prose.accepted_proof import validate_accepted_proof
from app.domain import canonical,digest
from test_portable_domain import domain,migrate_owned_database,seed_prose_history,restored


def test_accepted_proof_real_encrypted_restore_rebinds_only_structural_ids(domain,tmp_path):
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        source=db.scalar(select(m.GenerationAcceptance).where(m.GenerationAcceptance.generation_id==history['batch']))
        before=deepcopy(source.result['acceptedProofs']);source_id=source.id
    result=restored(domain,tmp_path);ids=result['idMap']
    with SessionLocal() as db:
        copied=db.get(m.GenerationAcceptance,ids[('generation_acceptance',source_id)])
        after=copied.result['acceptedProofs']
        assert len(after)==len(before)==1
        for old,proof in zip(before,after):
            assert proof['sourceHash']==old['sourceHash'] and proof['chapterIndex']==old['chapterIndex']
            assert proof['contentId']==ids[('chapter_content',old['contentId'])]!=old['contentId']
            assert proof['chapterId']==ids[('chapter',old['chapterId'])]!=old['chapterId']
            for old_part,part in zip(old['segments'],proof['segments']):
                assert part['blockId']==ids[('paragraph',old_part['blockId'])]!=old_part['blockId']
                assert {k:v for k,v in part.items() if k!='blockId'}=={k:v for k,v in old_part.items() if k!='blockId'}
                block=db.get(m.ContentBlock,part['blockId'])
                assert block.text==part['text'] and block.content_hash==part['blockHash']
        assert db.get(m.GenerationAcceptance,source_id).result['acceptedProofs']==before
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain['target']))==0


@pytest.mark.parametrize('fault',['source-hash','block-hash','offset','quote','chapter-index','missing-map','empty-map','deleted-proof-field'])
def test_accepted_source_corruption_never_installs_partial_target(domain,tmp_path,fault):
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        source=db.scalar(select(m.GenerationAcceptance).where(m.GenerationAcceptance.generation_id==history['batch']))
        value=deepcopy(source.result);proof=value['acceptedProofs'][0]
        if fault=='source-hash':proof['sourceHash']='0'*64
        elif fault=='block-hash':proof['segments'][0]['blockHash']='0'*64
        elif fault=='offset':proof['segments'][0]['sourceEnd']-=1
        elif fault=='quote':proof['segments'][0]['text']+='更改'
        elif fault=='chapter-index':proof['chapterIndex']=2
        elif fault=='missing-map':proof['segments'].pop()
        elif fault=='deleted-proof-field':del value['acceptedProofs']
        else:value['acceptedProofs']=[]
        source.result=value;db.commit()
    with pytest.raises(ArchiveError,match='BACKUP_GENERATION_ACCEPTED_PROOF_INVALID'):restored(domain,tmp_path)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain['target']))==0
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.owner_id==domain['target']))==0


@pytest.mark.parametrize('fault',['missing-accepted-chapter','borrow-identical-chapter','reordered-new-tail'])
def test_two_commits_require_the_exact_new_ordered_tail_even_when_bodies_are_identical(domain,tmp_path,fault):
    from app.prose.service import accept
    from app.prose.schemas import AcceptGeneration
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        batch=db.get(m.GenerationBatch,history['batch'])
        first=db.scalar(select(m.GenerationAcceptance).where(m.GenerationAcceptance.generation_id==batch.id))
        initial=db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==batch.id,m.GenerationChapterVersion.candidate_version==1,m.GenerationChapterVersion.chapter_index==1))
        for row in db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==batch.id,m.GenerationChapterVersion.candidate_version==2,m.GenerationChapterVersion.chapter_index.in_([2,3]))):
            row.text=initial.text;row.content_hash=digest(row.text);row.completeness='complete';row.durability='saved'
        db.commit()
        result=accept(db,domain['owner'],batch.id,AcceptGeneration(candidateVersion=2,prefixCount=3,
            expectedAcceptedPrefix=1,expectedHeadRevisionId=batch.last_accepted_revision_id,target='append',sourcePolicy='requireUnchanged'),'own-second-proof')
        second=db.scalar(select(m.GenerationAcceptance).where(m.GenerationAcceptance.revision_id==result['revisionId']))
        value=deepcopy(second.result)
        if fault=='missing-accepted-chapter':
            value['acceptedProofs']=value['acceptedProofs'][1:];value['chapterIds']=value['chapterIds'][1:];value['chapterCount']=1
        elif fault=='borrow-identical-chapter':
            earlier=first.result['acceptedProofs'][0];value['acceptedProofs'][0]['chapterId']=earlier['chapterId'];value['acceptedProofs'][0]['segments']=deepcopy(earlier['segments']);value['chapterIds'][0]=earlier['chapterId']
        else:
            revision=db.get(m.Revision,result['revisionId']);manifest=deepcopy(revision.manifest);manifest[-2:]=manifest[-2:][::-1];revision.manifest=manifest;revision.content_hash=digest(canonical(manifest))
        second.result=value;db.commit()
    with pytest.raises(ArchiveError,match='BACKUP_GENERATION_ACCEPTED_PROOF_INVALID'):restored(domain,tmp_path)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain['target']))==0
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.owner_id==domain['target']))==0
