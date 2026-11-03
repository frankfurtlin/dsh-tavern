"""Actual private AES restore of selection candidates; no provider dispatch."""
from copy import deepcopy
from pathlib import Path
import pytest
from sqlalchemy import select, func
from alembic import command
from alembic.config import Config
from app import models as m
from app.db import SessionLocal, engine
from app.config import settings
from app.domain import make_anchor, utf16_len, digest, canonical
from app.prose.schemas import SelectionEdit
from app.prose.schemas import SelectionRevisionAction
from app.prose.selection_edits import apply_selection, validate_selection_revision, select_revision
from app.portable.errors import ArchiveError
from app.portable.models import PortableBase, PORTABLE_TABLES
from app.assets.models import AssetsBase, ASSET_TABLES
from app.world.migrate import migrate
from test_portable_domain import domain, restored


@pytest.fixture(scope='session',autouse=True)
def migrate_owned_database():
    from owned_test_runtime import require_owned_runtime
    require_owned_runtime('selection_edit_portable_test', '/tmp/selection-edit-portable-private')
    command.upgrade(Config('alembic.ini'),'head');migrate()
    PortableBase.metadata.create_all(engine,tables=PORTABLE_TABLES)
    AssetsBase.metadata.create_all(engine,tables=ASSET_TABLES)


def seed(domain):
    with SessionLocal() as db:
        source=db.get(m.ContentBlock,domain['block']);size=utf16_len(source.text)
        start=make_anchor(domain['book'],domain['branch'],domain['revision'],source,2)
        end=make_anchor(domain['book'],domain['branch'],domain['revision'],source,size)
        data=SelectionEdit(startAnchor=start,endAnchor=end,replacementText='新的信件。',expectedSourceHeadRevisionId=domain['revision'])
        first=apply_selection(db,domain['owner'],domain['book'],data,'own-selection-portable-first')
        data=data.model_copy(update={'replacementText':'另一封信。','targetBranchId':first['branchId'],'expectedTargetHeadRevisionId':first['revisionId']})
        second=apply_selection(db,domain['owner'],domain['book'],data,'own-selection-portable-second')
        data=data.model_copy(update={'replacementText':None,'expectedTargetHeadRevisionId':second['revisionId']})
        last=apply_selection(db,domain['owner'],domain['book'],data,'own-selection-portable-restore')
        revision=db.get(m.Revision,first['revisionId']);block=db.get(m.ContentBlock,revision.manifest[0]['blocks'][0])
        anchor=make_anchor(domain['book'],first['branchId'],first['revisionId'],block,2)
        note=m.Bookmark(id=m.uid(),owner_id=domain['owner'],book_id=domain['book'],branch_id=first['branchId'],revision_id=first['revisionId'],anchor=anchor,note='第一候选的旧笔记')
        db.add(note);db.commit()
        return {'first':first,'second':second,'last':last,'block':block.id,'note':note.id,'text':source.text}


def test_aes_restore_preserves_all_candidates_and_exact_original_restore(domain,tmp_path):
    seeded=seed(domain);result=restored(domain,tmp_path);ids=result['idMap']
    with SessionLocal() as db:
        versions=[db.get(m.Revision,ids['revision',seeded[k]['revisionId']]) for k in ('first','second','last')]
        for row in versions:validate_selection_revision(row,db.get)
        source=db.get(m.Revision,ids['revision',domain['revision']])
        assert versions[-1].manifest==source.manifest and versions[-1].content_hash==source.content_hash
        assert versions[1].parent_revision_id==versions[0].id and versions[2].parent_revision_id==versions[1].id
        assert versions[0].provenance['selectionEdit']['baseRevisionId']==source.id
        assert versions[0].provenance['selectionEdit']['startAnchor']['blockId']==ids['paragraph',domain['block']]
        assert versions[0].provenance['selectionEdit']['blockId']==ids['paragraph',seeded['block']]
        assert db.get(m.ContentBlock,ids['paragraph',seeded['block']]).text=='林渡新的信件。'
        assert db.get(m.ContentBlock,ids['paragraph',domain['block']]).text==seeded['text']
        note=db.get(m.Bookmark,ids['note',seeded['note']]);assert note.revision_id==versions[0].id and note.anchor['blockId']==ids['paragraph',seeded['block']]
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id==domain['target']))==0
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain['target']))==0


def test_aes_restore_keeps_rechosen_candidate_and_second_exact_restore(domain,tmp_path):
    seeded=seed(domain)
    with SessionLocal() as db:
        action=SelectionRevisionAction(action='select_candidate',candidateRevisionId=seeded['first']['revisionId'],expectedSourceHeadRevisionId=domain['revision'],expectedTargetHeadRevisionId=seeded['last']['revisionId'])
        chosen=select_revision(db,domain['owner'],seeded['first']['branchId'],action,'own-portable-reselect')
        again=select_revision(db,domain['owner'],seeded['first']['branchId'],action,'own-portable-reselect')
        assert chosen==again
        undo=SelectionRevisionAction(action='restore_original',expectedSourceHeadRevisionId=domain['revision'],expectedTargetHeadRevisionId=chosen['revisionId'])
        restored_again=select_revision(db,domain['owner'],chosen['branchId'],undo,'own-portable-second-restore')
        db.commit()
    result=restored(domain,tmp_path);ids=result['idMap']
    with SessionLocal() as db:
        chosen_row=db.get(m.Revision,ids['revision',chosen['revisionId']]);last=db.get(m.Revision,ids['revision',restored_again['revisionId']]);source=db.get(m.Revision,ids['revision',domain['revision']])
        for row in (chosen_row,last):validate_selection_revision(row,db.get)
        assert chosen_row.sequence==4 and last.sequence==5 and last.parent_revision_id==chosen_row.id
        block=db.get(m.ContentBlock,chosen_row.manifest[0]['blocks'][0]);assert block.text=='林渡新的信件。'
        assert last.manifest==source.manifest and last.content_hash==source.content_hash
        old_note=db.get(m.Bookmark,ids['note',seeded['note']]);assert old_note.revision_id==ids['revision',seeded['first']['revisionId']]


@pytest.mark.parametrize('fault',['source-hash','replacement-text','replacement-bytes','manifest','delete-marker','delete-proof','delete-both','whole-family-downgrade','unwitnessed-manual','duplicate-introduction'])
def test_bad_selection_archive_rejects_before_target_writes(domain,tmp_path,fault):
    seeded=seed(domain)
    with SessionLocal() as db:
        revision=db.get(m.Revision,seeded['first']['revisionId']);provenance=deepcopy(revision.provenance)
        proof=provenance['selectionEdit']
        if fault=='source-hash':proof['sourceProofs'][0]['contentHash']='0'*64
        elif fault=='replacement-text':proof['replacementText']='偷偷换文。'
        elif fault=='replacement-bytes':block=db.get(m.ContentBlock,seeded['block']);block.text='篡改候选。';block.content_hash=digest(block.text)
        elif fault=='manifest':revision.manifest=deepcopy(db.get(m.Revision,domain['revision']).manifest);revision.content_hash=digest(canonical(revision.manifest))
        elif fault=='delete-marker':provenance.pop('selectionEditSchema')
        elif fault=='delete-proof':provenance.pop('selectionEdit')
        elif fault=='delete-both':provenance.pop('selectionEditSchema');provenance.pop('selectionEdit')
        revision.provenance=provenance;db.commit()
        if fault=='whole-family-downgrade':
            for candidate in db.scalars(select(m.Revision).where(m.Revision.branch_id==seeded['first']['branchId'])):
                candidate.provenance={'kind':'manual'}
            db.commit()
        elif fault=='unwitnessed-manual':
            db.add(m.ContentBlock(id=m.uid(),owner_id=domain['owner'],book_id=domain['book'],chapter_id=revision.manifest[0]['id'],text='无来源修订',content_hash=digest('无来源修订'),source='manual'))
            db.commit()
        elif fault=='duplicate-introduction':
            duplicate=m.Revision(id=m.uid(),owner_id=domain['owner'],branch_id=revision.branch_id,parent_revision_id=seeded['last']['revisionId'],sequence=4,manifest=deepcopy(revision.manifest),content_hash=revision.content_hash,provenance=deepcopy(provenance))
            db.add(duplicate);db.get(m.Branch,revision.branch_id).head_revision_id=duplicate.id;db.commit()
    with pytest.raises(ArchiveError):restored(domain,tmp_path)
    with SessionLocal() as db:
        for cls in (m.Book,m.Branch,m.Revision,m.ContentBlock,m.Bookmark,m.Task,m.GenerationBatch,m.KnowledgeSnapshot,m.CharacterIdentity):
            assert db.scalar(select(func.count()).select_from(cls).where(cls.owner_id==domain['target']))==0
