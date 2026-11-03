"""Real encrypted nonempty basis copies, distinct branch actors, zero providers."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app import models as m
from app.domain import canonical,digest,make_anchor,utf16_len
from app.prose.accepted_basis import collect_accepted_basis,candidate_bindings,SCHEMA
from app.prose.events import from_review
from app.prose.service import accept
from app.prose.schemas import AcceptGeneration
from app.portable.errors import ArchiveError
from test_portable_domain import domain,migrate_owned_database,restored,encrypted
from test_prose_events_portable import seed_events
from app.portable.package import prepare_backup
from app.portable.preflight import preflight_worlds


def seed_carried_basis(domain):
    history,_=seed_events(domain)
    with SessionLocal() as db:
        first=db.get(m.GenerationBatch,history['batch']);source=db.get(m.Task,first.task_id)
        initial=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.book_id==domain['book'],m.KnowledgeSnapshot.branch_id==domain['branch']))
        source.checkpoint={**source.checkpoint,'snapshotId':initial.id,
            'acceptedEventBasis':collect_accepted_basis(db,domain['owner'],first.origin_anchor,db.get(m.Revision,first.origin_anchor['revisionId']),observed=False)}
        source.payload={**source.payload,'acceptedBasisSchema':SCHEMA};first.request={**first.request,'acceptedBasisSchema':SCHEMA}
        db.commit()

        def another(revision_id,actor,*,accepted=False):
            revision=db.get(m.Revision,revision_id);block=db.get(m.ContentBlock,revision.manifest[-1]['blocks'][-1])
            anchor=make_anchor(domain['book'],revision.branch_id,revision.id,block,utf16_len(block.text))
            data=deepcopy(initial.data);data['characters'][0]['id']=actor
            for evidence in data['evidence']:
                p=db.get(m.ContentBlock,evidence['anchor']['blockId'])
                evidence['anchor']=make_anchor(domain['book'],revision.branch_id,revision.id,p,evidence['anchor']['start'],evidence['anchor']['end'])
            snapshot=m.KnowledgeSnapshot(id=m.uid(),owner_id=domain['owner'],book_id=domain['book'],branch_id=revision.branch_id,
                revision_id=revision.id,boundary=anchor,scope_hash=digest(actor),profile_version='fixture-v1',data=data,completeness='complete')
            db.add(snapshot);db.flush()
            basis=collect_accepted_basis(db,domain['owner'],anchor,revision,observed=False)
            request={**deepcopy(first.request),'originAnchor':anchor,'expectedHeadRevisionId':revision.id,'chapterCount':1,
                'instruction':'字面 local-0、c1:e1 保留','target':{'mode':'new_child_branch','name':'另一批','branchId':None}}
            task=m.Task(id=m.uid(),owner_id=domain['owner'],task_type='story_flow',state='failed',
                payload={'bookId':domain['book'],'anchor':anchor,'protocol':'reader-prose-v1','profileId':history['profile'],'operation':'continue_from_anchor','acceptedBasisSchema':SCHEMA},
                checkpoint={'acceptedEventBasis':basis,'snapshotId':snapshot.id},result={'phase':'incomplete'},provider_state='response_received',persistence_state='saved',usage_state='settled')
            db.add(task);db.flush()
            batch=m.GenerationBatch(id=m.uid(),owner_id=domain['owner'],task_id=task.id,book_id=domain['book'],origin_anchor=anchor,
                request=request,profile_snapshot=deepcopy(first.profile_snapshot),candidate_version=1,accepted_prefix=0,version_times={'1':m.now().isoformat()})
            db.add(batch);db.flush()
            from app.domain import prefix_blocks
            prefix=[{'sourceParagraphId':p['blockId'],'text':p['text']} for p in prefix_blocks(db,revision,anchor,domain['owner'])]
            bindings=candidate_bindings(basis,snapshot,batch.id,prefix)
            body='林渡把原ID local-0 信封放在岸上。'
            event={'kind':'item','subjectId':actor,'certainty':'narrated','confidence':900,'statement':'字面 local-0、c1:e1 保留','quote':body,
                'change':{'item':'原ID local-0 信封','fromHolderId':actor,'toHolderId':actor,'location':'岸上'}}
            ledger=from_review({'text':json.dumps({'events':[event]},ensure_ascii=False),'finishReason':'stop'},body,1,{actor},[])
            task.checkpoint={**task.checkpoint,'acceptedCandidateBindings':bindings,'proseEventLedger':{'1':{'1':ledger}}}
            db.add(m.GenerationChapterVersion(id=m.uid(),owner_id=domain['owner'],generation_id=batch.id,candidate_version=1,chapter_index=1,
                content_id=m.uid(),title='下一章',text=body,content_hash=digest(body),stream_version=1,snapshot_version=1,completeness='complete',durability='saved'))
            db.commit()
            if accepted:
                receipt=accept(db,domain['owner'],batch.id,AcceptGeneration(candidateVersion=1,prefixCount=1,expectedAcceptedPrefix=0,
                    expectedHeadRevisionId=revision.id,target='child',sourcePolicy='requireUnchanged'),'own-carry-'+batch.id)
                return batch.id,task.id,snapshot.id,basis,receipt['revisionId']
            return batch.id,task.id,snapshot.id,basis,revision.id
        second=another(history['acceptanceRevision'],'second-person',accepted=True)
        third=another(second[4],'third-person')
        assert len(third[3]['sources'])==2 and len(third[3]['subjects'])==2
        assert len({s['sourceBranchId'] for s in third[3]['sources']})==2
        assert len({s['canonicalId'] for s in third[3]['subjects']})==1
        return history,second,third


def test_nonempty_encrypted_cross_batch_basis_restores_exact_source_scopes_and_readonly_history(domain,tmp_path):
    history,second,third=seed_carried_basis(domain);before=third[3]
    archive=encrypted(domain,tmp_path)
    with archive.open('rb') as stream,prepare_backup(stream,'synthetic-test-password',tmp_path/'preview') as prepared:
        assert preflight_worlds(prepared)['domainObjectsChecked']>0
    receipt=restored(domain,tmp_path);ids=receipt['idMap']
    with SessionLocal() as db:
        task=db.get(m.Task,ids[('task',third[1])]);after=task.checkpoint['acceptedEventBasis']
        assert task.payload['importedHistory'] and task.state=='cancelled' and task.lease_owner is None
        assert after['hash']!=before['hash'] and after['scope']['bookId']==ids[('book',domain['book'])]
        assert after['hash']==digest(canonical({k:v for k,v in after.items() if k!='hash'}))
        for old,new in zip(before['sources'],after['sources']):
            for key,kind in [('generationId','generation_batch'),('contentId','chapter_content'),('acceptanceId','generation_acceptance'),('revisionId','revision'),('sourceBranchId','branch')]:
                assert new[key]==ids[(kind,old[key])]!=old[key]
            assert new['sourceHash']==old['sourceHash'] and new['ledgerHash']!=old['ledgerHash'] and new['proofHash']!=old['proofHash']
        actors=[]
        for old,new in zip(before['subjects'],after['subjects']):
            actor=ids[('reader_entity',old['sourceBranchId']+':'+old['subjectId'])];actors.append(actor)
            assert new['subjectId']==actor!=old['subjectId'] and new['name']==old['name']
            assert new['primary']['blockId']==ids[('paragraph',old['primary']['blockId'])]
            assert new['primary']['quote']==old['primary']['quote'] and new['canonicalId']!=old['canonicalId']
        assert len(set(actors))==2 and len({s['canonicalId'] for s in after['subjects']})==1
        assert [e['ref']['localEventId'] for e in after['events']]==[e['ref']['localEventId'] for e in before['events']]
        assert [e['entry']['quote'] for e in after['events']]==[e['entry']['quote'] for e in before['events']]
        assert after['state']['items']['原ID local-0 信封']['location']=='岸上'
        bindings=task.checkpoint['acceptedCandidateBindings'];original_task=db.get(m.Task,third[1])
        assert bindings[0]['runId']==ids[('generation_batch',third[0])]
        assert bindings[0]['subjectId']==ids[('reader_entity',before['scope']['branchId']+':third-person')]
        assert task.checkpoint['acceptedSourceSnapshotId']==ids[('knowledge_snapshot',third[2])]
        assert original_task.checkpoint['acceptedEventBasis']==before
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain['target']))==0
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id==domain['target']))==0


@pytest.mark.parametrize('fault',['hash','events','binding','source-ref','future-prefix','identity','state','warnings','delete-basis','delete-basis-and-bindings','delete-task-marker','delete-request-marker'])
def test_rehashed_poisoned_basis_is_rejected_before_target_install(domain,tmp_path,fault):
    _,_,third=seed_carried_basis(domain)
    with SessionLocal() as db:
        task=db.get(m.Task,third[1]);checkpoint=deepcopy(task.checkpoint);basis=checkpoint['acceptedEventBasis']
        if fault=='hash':basis['hash']='0'*64
        elif fault=='events':basis['events']=[]
        elif fault=='binding':checkpoint['acceptedCandidateBindings'][0]['canonicalId']='reader-proof:'+'0'*64
        elif fault=='source-ref':basis['sources'][0]['generationId']=third[0]
        elif fault=='future-prefix':basis['prefix'][-1]['limit']-=1
        elif fault=='identity':basis['subjects'][0]['primary']['start']=9
        elif fault=='state':basis['state']['items']['原ID local-0 信封']['location']='不存在的远岸'
        elif fault=='warnings':basis['warnings'].append({'malformed':'unhashable'})
        elif fault.startswith('delete-basis'):
            del checkpoint['acceptedEventBasis']
            if fault=='delete-basis-and-bindings':del checkpoint['acceptedCandidateBindings']
        elif fault=='delete-task-marker':task.payload={key:value for key,value in task.payload.items() if key!='acceptedBasisSchema'}
        elif fault=='delete-request-marker':
            batch=db.get(m.GenerationBatch,third[0]);batch.request={key:value for key,value in batch.request.items() if key!='acceptedBasisSchema'}
        if fault not in ('hash','warnings'):basis['hash']=digest(canonical({k:v for k,v in basis.items() if k!='hash'}))
        task.checkpoint=checkpoint;db.commit()
    with pytest.raises(ArchiveError,match='BACKUP_ACCEPTED_BASIS_INVALID'):restored(domain,tmp_path)
    with SessionLocal() as db:
        for model in (m.Book,m.Task,m.GenerationBatch,m.KnowledgeSnapshot,m.UsageRecord):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain['target']))==0
