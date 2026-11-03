"""Owned AES archive migration of immutable Unicode prefixes; no provider use."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from sqlalchemy import select,func
from alembic import command
from alembic.config import Config
from app.db import SessionLocal,engine
from app.config import settings
from app import models as m
from app.domain import make_anchor,digest,utf16_len
from app.portable.errors import ArchiveError
from app.portable.models import PortableBase,PORTABLE_TABLES
from app.assets.models import AssetsBase,ASSET_TABLES
from app.world.migrate import migrate
from app.prose.service import accept
from app.prose.schemas import AcceptGeneration
from app.prose.prefix_fragments import fragment_roots
from app.prose.accepted_basis import collect_accepted_basis,SCHEMA,candidate_bindings,database_roots
from app.prose.events import from_review
from test_portable_domain import domain,restored


@pytest.fixture(scope='session',autouse=True)
def migrate_owned_database():
    from owned_test_runtime import require_owned_runtime
    require_owned_runtime('char_prefix_portable_test', '/tmp/char-prefix-portable-private')
    command.upgrade(Config('alembic.ini'),'head');migrate()
    PortableBase.metadata.create_all(engine,tables=PORTABLE_TABLES)
    AssetsBase.metadata.create_all(engine,tables=ASSET_TABLES)


def seed_char_history(domain, *, nested=False):
    with SessionLocal() as db:
        snapshot=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain['owner']))
        def one(revision,block,limit,label):
            anchor=make_anchor(domain['book'],revision.branch_id,revision.id,block,limit)
            basis=collect_accepted_basis(db,domain['owner'],anchor,revision,observed=False)
            source_snapshot=snapshot
            if revision.branch_id!=snapshot.branch_id:
                primary=db.get(m.ContentBlock,revision.manifest[0]['blocks'][0])
                data=deepcopy(snapshot.data)
                data['evidence'][0]['anchor']=make_anchor(domain['book'],revision.branch_id,revision.id,primary,0,2)
                source_snapshot=m.KnowledgeSnapshot(id=m.uid(),owner_id=domain['owner'],book_id=domain['book'],
                    branch_id=revision.branch_id,revision_id=revision.id,boundary=anchor,scope_hash=digest(label),
                    profile_version='own-synthetic-prefix',data=data,completeness='complete')
                db.add(source_snapshot);db.flush()
            profile=m.uid()
            task=m.Task(id=m.uid(),owner_id=domain['owner'],task_type='story_flow',state='failed',
                payload={'bookId':domain['book'],'anchor':anchor,'protocol':'reader-prose-v1',
                    'profileId':profile,'operation':'counterfactual','acceptedBasisSchema':SCHEMA},
                checkpoint={'acceptedEventBasis':basis,'snapshotId':source_snapshot.id},
                result={'phase':'incomplete'},provider_state='response_received',persistence_state='saved',usage_state='settled')
            db.add(task);db.flush()
            request={'protocol':'reader-prose-v1','originAnchor':anchor,'operation':'counterfactual',
                'chapterCount':1,'chapterLength':'short','instruction':'合成字符分叉',
                'profileId':profile,'expectedHeadRevisionId':revision.id,'acceptedBasisSchema':SCHEMA,
                'target':{'mode':'new_child_branch','name':label,'branchId':None}}
            batch=m.GenerationBatch(id=m.uid(),owner_id=domain['owner'],task_id=task.id,book_id=domain['book'],
                origin_anchor=anchor,request=request,profile_snapshot={'id':profile,'version':1,
                    'name':'合成模型','model_id':'never-dispatched-fixture'},candidate_version=1,accepted_prefix=0,
                version_times={'1':m.now().isoformat()})
            db.add(batch);db.flush()
            roots=database_roots(db,domain['owner'],revision,domain['book'])
            from app.domain import prefix_blocks
            prefix=[{'sourceParagraphId':roots.get(p['blockId'],p['blockId']),'text':p['text']}
                for p in prefix_blocks(db,revision,anchor,domain['owner'])]
            bindings=candidate_bindings(basis,source_snapshot,batch.id,prefix,roots)
            if revision.branch_id!=domain['branch']:
                assert len(bindings)==1 and bindings[0]['canonicalId']==basis['subjects'][0]['canonicalId']
            task.checkpoint={**task.checkpoint,'acceptedCandidateBindings':bindings}
            text='林渡在岸上😀。后一段未来事件尚未读到。'
            event={'kind':'location','subjectId':'local-0','certainty':'narrated','confidence':900,
                'statement':'林渡在岸上','quote':'林渡在岸上😀。','change':{'from':None,'to':'岸上'}}
            ledger=from_review({'text':json.dumps({'events':[event]},ensure_ascii=False),'finishReason':'stop'},text,1,{'local-0'},[])
            assert ledger['status']=='model-proposed',ledger
            task.checkpoint={**task.checkpoint,'proseEventLedger':{'1':{'1':ledger}}}
            db.add(m.GenerationChapterVersion(id=m.uid(),owner_id=domain['owner'],generation_id=batch.id,
                candidate_version=1,chapter_index=1,content_id=m.uid(),title='合成新章',text=text,
                content_hash=digest(text),stream_version=1,snapshot_version=1,completeness='complete',durability='saved'))
            db.commit()
            result=accept(db,domain['owner'],batch.id,AcceptGeneration(candidateVersion=1,prefixCount=1,
                expectedAcceptedPrefix=0,expectedHeadRevisionId=revision.id,target='child',sourcePolicy='requireUnchanged'),
                'own-char-'+batch.id)
            child=db.get(m.Revision,result['revisionId']);fragment=child.provenance['paragraphPrefixes'][0]['blockId']
            return batch,task,child,fragment
        original=db.get(m.Revision,domain['revision']);block=db.get(m.ContentBlock,domain['block'])
        first=one(original,block,2,'字符分叉一')
        second=None
        if nested:
            parent=first[2];accepted_block=db.get(m.ContentBlock,parent.manifest[-1]['blocks'][0])
            # Include the entire first sentence and emoji, exclude its later body.
            second=one(parent,accepted_block,utf16_len('林渡在岸上😀。'),'字符分叉二')
        return {'firstBatch':first[0].id,'firstRevision':first[2].id,'firstFragment':first[3],
            **({'secondBatch':second[0].id,'secondRevision':second[2].id,'secondFragment':second[3]} if second else {})}


@pytest.mark.parametrize('nested',[False,True])
def test_aes_restore_remaps_exact_origin_and_preserves_read_prefix_without_provider(domain,tmp_path,nested):
    history=seed_char_history(domain,nested=nested)
    with SessionLocal() as db:
        original_text=db.get(m.ContentBlock,domain['block']).text
        last=db.get(m.Revision,history['secondRevision'] if nested else history['firstRevision'])
        last_block=db.get(m.ContentBlock,last.manifest[-1]['blocks'][-1])
        anchor=make_anchor(domain['book'],last.branch_id,last.id,last_block,utf16_len(last_block.text))
        before=collect_accepted_basis(db,domain['owner'],anchor,last,observed=False)
        if nested:assert len(before['sources'])==2,before
    receipt=restored(domain,tmp_path);ids=receipt['idMap']
    with SessionLocal() as db:
        first=db.get(m.Revision,ids['revision',history['firstRevision']])
        old=db.get(m.Revision,history['firstRevision'])
        proof=first.provenance['paragraphPrefixes'][0]
        assert proof['prefixSourceBlockId']==ids['paragraph',domain['block']]
        assert proof['prefixSourceRevisionId']==ids['revision',domain['revision']]
        assert proof['blockId']==ids['paragraph',history['firstFragment']]
        assert proof['sourceHash']==old.provenance['paragraphPrefixes'][0]['sourceHash']
        assert db.get(m.ContentBlock,proof['blockId']).text=='林渡'
        assert db.get(m.ContentBlock,ids['paragraph',domain['block']]).text==original_text
        chain=[first,db.get(m.Revision,ids['revision',domain['revision']])]
        if nested:
            second=db.get(m.Revision,ids['revision',history['secondRevision']]);chain.insert(0,second)
            end=db.get(m.ContentBlock,ids['paragraph',history['secondFragment']])
            assert end.text=='林渡在岸上😀。'
            task=db.get(m.Task,db.get(m.GenerationBatch,ids['generation_batch',history['secondBatch']]).task_id)
            carried=task.checkpoint['acceptedEventBasis']
            assert len(carried['sources'])==1 and len(carried['events'])==1,carried
            assert carried['events'][0]['entry']['quote']=='林渡在岸上😀。'
            assert carried['subjects'][0]['primary']['blockId']==ids['paragraph',domain['block']]
            assert task.state=='cancelled' and task.payload['importedHistory']
        roots=fragment_roots(chain,db.get)
        assert roots[proof['blockId']]==ids['paragraph',domain['block']]
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain['target']))==0
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id==domain['target']))==0


@pytest.mark.parametrize('fault',['source-hash','prefix-hash','source-ref','half-surrogate','prefix-text','delete-marker','delete-proof','delete-both','duplicate'])
def test_poisoned_prefix_archive_has_zero_target_namespace(domain,tmp_path,fault):
    history=seed_char_history(domain)
    with SessionLocal() as db:
        revision=db.get(m.Revision,history['firstRevision']);provenance=deepcopy(revision.provenance)
        proof=provenance['paragraphPrefixes'][0];part=db.get(m.ContentBlock,history['firstFragment'])
        if fault=='source-hash':proof['sourceHash']='0'*64
        elif fault=='prefix-hash':proof['prefixHash']='0'*64
        elif fault=='source-ref':proof['prefixSourceBlockId']=m.uid()
        elif fault=='half-surrogate':
            source=db.get(m.ContentBlock,domain['block'])
            proof['limit']=utf16_len(source.text[:source.text.index('🙂')])+1
        elif fault=='prefix-text':part.text='别的人';part.content_hash=digest(part.text);proof['prefixHash']=part.content_hash
        elif fault=='delete-marker':provenance.pop('paragraphPrefixSchema')
        elif fault=='delete-proof':provenance.pop('paragraphPrefixes')
        elif fault=='delete-both':
            provenance.pop('paragraphPrefixSchema');provenance.pop('paragraphPrefixes')
        elif fault=='duplicate':provenance['paragraphPrefixes'].append(deepcopy(proof))
        revision.provenance=provenance;db.commit()
    with pytest.raises(ArchiveError):restored(domain,tmp_path)
    with SessionLocal() as db:
        for model in (m.Book,m.Branch,m.Revision,m.ContentBlock,m.Task,m.GenerationBatch,m.KnowledgeSnapshot):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain['target']))==0
