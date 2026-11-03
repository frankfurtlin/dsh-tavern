"""Frozen accepted-story context selected through revision ancestry, not book tasks."""
from copy import deepcopy
from sqlalchemy import select
from ..domain import canonical, digest, owned, utf16_len, utf16_slice
from ..models import (Revision, Branch, ContentBlock, GenerationAcceptance,
    GenerationBatch, GenerationChapterVersion, Task, KnowledgeSnapshot)
from ..knowledge import safe_scope
from ..character_identity import reader_proof
from ..errors import Problem
from .accepted_proof import validate_accepted_proof
from .accepted_events import derive_visible_accepted_events
from .accepted_event_state import fold_visible_accepted_events
from .events import validate

SCHEMA = 'accepted-events-basis-v1'


def validate_basis_contract(batch,task,*,check_hash=True):
    """Independent request/payload markers fence whole-checkpoint downgrade."""
    markers=[batch.request.get('acceptedBasisSchema'),task.payload.get('acceptedBasisSchema')]
    basis=task.checkpoint.get('acceptedEventBasis')
    if all('acceptedBasisSchema' not in envelope for envelope in (batch.request,task.payload)) and 'acceptedEventBasis' not in task.checkpoint and 'acceptedCandidateBindings' not in task.checkpoint:return
    if markers!=[SCHEMA,SCHEMA] or not isinstance(basis,dict) or basis.get('schema')!=SCHEMA:
        raise Problem('ACCEPTED_BASIS_INVALID','本次续写依据缺失或格式不一致，正文仍保留')
    try:
        from ..portable.accepted_basis import validate_shape,references
        validate_shape(basis,check_hash=check_hash);references(basis)
        if basis['scope']!=batch.origin_anchor:raise ValueError('accepted basis scope')
    except Exception as error:
        raise Problem('ACCEPTED_BASIS_INVALID','本次续写依据缺失或格式不一致，正文仍保留') from error


def actor_ids(entries):
    result=set()
    for event in entries:
        if event['subjectId'] is not None:result.add(event['subjectId'])
        change=event['change']
        if event['kind']=='item':result.update(value for value in (change['fromHolderId'],change['toHolderId']) if value is not None)
        elif event['kind']=='knowledge':result.update(change['learnedByIds'])
        elif event['kind']=='relationship':result.add(change['otherId'])
    return result


def identity_proof(character, snapshot, prefix, roots=None):
    proof=reader_proof(character,snapshot.data)
    if not proof:return None
    root=(roots or {}).get(proof['blockId'],proof['blockId'])
    visible=next((p for p in prefix if p['sourceParagraphId']==root),None)
    if not visible:return None
    label=proof['originalLabel'];end=proof['start']+utf16_len(label)
    if end>utf16_len(visible['text']) or utf16_slice(visible['text'],proof['start'],end)!=label:return None
    return {'blockId':root,'start':proof['start'],'quote':label}


def identity_key(book_id, proof):
    return 'reader-proof:'+digest(canonical({'bookId':book_id,**proof}))


def basis_value(value):return {key:item for key,item in value.items() if key!='hash'}


def prompt_basis(value):
    """Keep all visible events/evidence; internal hashes are not repeated as narrative."""
    return {'schema':'accepted-event-context-v1','basisHash':value['hash'],'scope':deepcopy(value['scope']),
        'events':[{key:deepcopy(event[key]) for key in ('ref','entry','dependencies','frameRef','temporalFrame','evidence')} for event in value['events']],
        'subjects':[{key:deepcopy(person[key]) for key in ('runId','subjectId','canonicalId','name','primary')} for person in value['subjects']],
        'status':value['status'],'warnings':deepcopy(value['warnings'])}


def display_basis(value,db):
    """Owned public view of the exact frozen scope; no provider/private checkpoint."""
    identifiers=list(dict.fromkeys([value['scope']['blockId']]+[proof['paragraphId'] for event in value['events'] for proof in event['evidence']]))
    blocks={block.id:block for block in db.scalars(select(ContentBlock).where(ContentBlock.book_id==value['scope']['bookId'],ContentBlock.id.in_(identifiers)))}
    chapters=[{'paragraphId':identifier,'chapterId':blocks[identifier].chapter_id} for identifier in identifiers if identifier in blocks]
    return dict(prompt_basis(value),sourceCount=len(value['sources']),paragraphChapters=chapters,
        originCount=len({source['generationId'] for source in value['sources']}),prefixCount=len(value['prefix']))


def collect_accepted_basis(db, owner, anchor, revision, *, observed=True):
    from ..domain import prefix_blocks
    blocks=safe_scope(db,anchor,revision,owner) if observed else prefix_blocks(db,revision,anchor,owner)
    return _collect_basis(_DatabaseSources(db,owner),owner,anchor,revision,blocks)


class _DatabaseSources:
    def __init__(self,db,owner):self.db,self.owner=db,owner
    def get(self,model,identifier):return owned(self.db,model,identifier,self.owner)
    def acceptance(self,revision):return self.db.scalar(select(GenerationAcceptance).where(GenerationAcceptance.owner_id==self.owner,GenerationAcceptance.revision_id==revision))
    def acceptances(self,generation,revisions):return list(self.db.scalars(select(GenerationAcceptance).where(GenerationAcceptance.owner_id==self.owner,GenerationAcceptance.generation_id==generation,GenerationAcceptance.revision_id.in_(list(revisions)))))
    def chapter(self,generation,version,index):return self.db.scalar(select(GenerationChapterVersion).where(GenerationChapterVersion.owner_id==self.owner,GenerationChapterVersion.generation_id==generation,GenerationChapterVersion.candidate_version==version,GenerationChapterVersion.chapter_index==index))
    def snapshot(self,task):
        identifier=task.checkpoint.get('snapshotId') or task.checkpoint.get('acceptedSourceSnapshotId')
        return self.get(KnowledgeSnapshot,identifier) if identifier else None
    def check_contract(self,batch,task):validate_basis_contract(batch,task)


class _RowSources:
    KINDS={Revision:'revision',Branch:'branch',ContentBlock:'paragraph',GenerationAcceptance:'generation_acceptance',GenerationBatch:'generation_batch',GenerationChapterVersion:'chapter_version',Task:'task',KnowledgeSnapshot:'knowledge_snapshot'}
    def __init__(self,rows,*,rebasing=False):self.rows,self.rebasing=rows,rebasing
    def get(self,model,identifier):
        row=self.rows.get(self.KINDS[model],{}).get(identifier)
        if row is None:raise ValueError('accepted basis source closure')
        return row
    def acceptance(self,revision):return next((r for r in self.rows.get('generation_acceptance',{}).values() if r.revision_id==revision),None)
    def acceptances(self,generation,revisions):return [r for r in self.rows.get('generation_acceptance',{}).values() if r.generation_id==generation and r.revision_id in revisions]
    def chapter(self,generation,version,index):return next((r for r in self.rows.get('chapter_version',{}).values() if r.generation_id==generation and r.candidate_version==version and r.chapter_index==index),None)
    def snapshot(self,task):
        identifier=task.checkpoint.get('snapshotId') or task.checkpoint.get('acceptedSourceSnapshotId')
        return self.get(KnowledgeSnapshot,identifier) if identifier else None
    def check_contract(self,batch,task):validate_basis_contract(batch,task,check_hash=not self.rebasing)


def revision_roots(source, revision, book_id):
    from .prefix_fragments import fragment_roots
    chain=[];seen=set();current=revision
    while current is not None:
        if current.id in seen or len(chain)>=1000:
            raise ValueError('paragraph prefix source chain')
        branch=source.get(Branch,current.branch_id)
        if branch.book_id!=book_id:
            raise ValueError('paragraph prefix source book')
        chain.append(current);seen.add(current.id)
        current=source.get(Revision,current.parent_revision_id) if current.parent_revision_id else None
    return fragment_roots(chain,source.get)


def database_roots(db, owner, revision, book_id):
    return revision_roots(_DatabaseSources(db,owner),revision,book_id)


def collect_basis_from_rows(rows,anchor,*,rebasing=False):
    """Private backup preflight uses the same collector before any target rows exist."""
    source=_RowSources(rows,rebasing=rebasing);revision=source.get(Revision,anchor['revisionId']);branch=source.get(Branch,anchor['branchId'])
    if revision.branch_id!=branch.id or branch.book_id!=anchor['bookId']:raise ValueError('accepted basis scope')
    blocks=[];found=False
    for chapter in revision.manifest:
        for identifier in chapter['blocks']:
            block=source.get(ContentBlock,identifier)
            if block.book_id!=anchor['bookId'] or block.chapter_id!=chapter['id'] or block.content_hash!=digest(block.text):raise ValueError('accepted basis prefix bytes')
            text=utf16_slice(block.text,0,anchor['end']) if identifier==anchor['blockId'] else block.text
            if text:blocks.append({'blockId':identifier,'chapterId':chapter['id'],'text':text,'source':block.source})
            if identifier==anchor['blockId']:found=True;break
        if found:break
    if not found:raise ValueError('accepted basis anchor membership')
    return _collect_basis(source,revision.owner_id,anchor,revision,blocks)


def _collect_basis(source, owner, anchor, revision, blocks):
    if len(blocks)>10000:raise Problem('CONTEXT_BUDGET_EXCEEDED','已读事件来源超过窗口，未调用模型')
    revisions,chain,seen={},[],set()
    current=revision
    while current is not None:
        if current.id in seen or len(chain)>=1000:raise Problem('ACCEPTED_BASIS_INVALID','故事来源链无效，正文仍保留')
        branch=source.get(Branch,current.branch_id)
        if branch.book_id!=anchor['bookId']:raise Problem('ACCEPTED_BASIS_INVALID','故事来源不属于本书')
        seen.add(current.id);revisions[current.id]=current;chain.append(current)
        current=source.get(Revision,current.parent_revision_id) if current.parent_revision_id else None
    from .prefix_fragments import fragment_roots
    try:
        roots = fragment_roots(chain, source.get)
    except (ValueError, KeyError, TypeError) as error:
        raise Problem('ACCEPTED_BASIS_INVALID','已读字符前缀来源不一致，正文仍保留') from error
    prefix=[{'paragraphId':b['blockId'],'paragraphRevision':1,'sourceParagraphId':roots.get(b['blockId'],b['blockId']),'text':b['text'],'textHash':digest(b['text'])} for b in blocks]
    positions={p['sourceParagraphId']:i for i,p in enumerate(prefix)}
    if len(positions)!=len(prefix):
        raise Problem('ACCEPTED_BASIS_INVALID','已读前缀有重复来源，正文仍保留')
    receipts=[];warnings=set()
    for source_revision in reversed(chain):
        provenance=source_revision.provenance
        if not isinstance(provenance,dict) or provenance.get('type')!='ai':continue
        acceptance=source.acceptance(source_revision.id)
        if acceptance is None or acceptance.result.get('acceptedProofSchema')!='accepted-content-proof-v1' or provenance.get('acceptedProofSchema')!='accepted-content-proof-v1':continue
        result=acceptance.result;proofs=result.get('acceptedProofs')
        if not isinstance(proofs,list):raise Problem('ACCEPTED_BASIS_INVALID','接受来源凭据不完整，正文仍保留')
        visible=[proof for proof in proofs if isinstance(proof,dict) and any(part.get('blockId') in positions for part in proof.get('segments',[]))]
        receipts.append((source_revision,acceptance,proofs,visible))
    highest={}
    for _,a,_,visible in receipts:
        for proof in visible:highest[a.generation_id]=max(highest.get(a.generation_id,0),proof['chapterIndex'])
    selected=[];covered=set();unavailable_origins=set()
    for source_revision,acceptance,proofs,_ in receipts:
        if acceptance.generation_id not in highest:continue
        batch=source.get(GenerationBatch,acceptance.generation_id)
        if batch.book_id!=anchor['bookId']:raise Problem('ACCEPTED_BASIS_INVALID','接受来源不属于本书')
        source_task=source.get(Task,batch.task_id)
        source.check_contract(batch,source_task)
        # Exact ordered manifest tail and accepted interval, including old parent receipts.
        from ..portable.prose_history import accepted_manifest_tail
        parent_receipts=source.acceptances(batch.id,revisions)
        block_ids={identifier for r in (source_revision,revisions[source_revision.parent_revision_id]) for chapter in r.manifest for identifier in chapter['blocks']}
        paragraph_rows={identifier:source.get(ContentBlock,identifier) for identifier in block_ids}
        try:accepted_manifest_tail(acceptance,batch,source_revision,acceptance.result,{'revision':revisions,'paragraph':paragraph_rows,'generation_acceptance':{a.id:a for a in parent_receipts}})
        except (ValueError,KeyError,TypeError) as error:raise Problem('ACCEPTED_BASIS_INVALID','接受回执与故事来源不一致，正文仍保留') from error
        for proof in proofs:
            if proof['chapterIndex']>highest[batch.id]:continue
            row=source.chapter(batch.id,acceptance.candidate_version,proof['chapterIndex'])
            if row is None or row.completeness!='complete' or row.durability!='saved':raise Problem('ACCEPTED_BASIS_INVALID','已接受正文来源版本缺失')
            chapter=next((c for c in source_revision.manifest if c['id']==proof['chapterId']),None)
            try:validate_accepted_proof(proof,row.text,row.content_id,row.chapter_index,chapter['id'],chapter['blocks'])
            except (ValueError,TypeError,KeyError) as error:raise Problem('ACCEPTED_BASIS_INVALID','接受正文凭据无效，正文仍保留') from error
            for part in proof['segments']:
                p=paragraph_rows[part['blockId']]
                if p.book_id!=batch.book_id or p.text!=part['text'] or p.content_hash!=part['blockHash']:raise Problem('ACCEPTED_BASIS_INVALID','接受段落字节不一致')
                if p.id in positions:covered.add(p.id)
            ledger=source_task.checkpoint.get('proseEventLedger',{}).get(str(acceptance.candidate_version),{}).get(str(row.chapter_index))
            if ledger is None:warnings.add('SOURCE_EVENTS_UNAVAILABLE');unavailable_origins.add(batch.id);continue
            validate(ledger,row.text)
            if ledger['status']=='unavailable':warnings.add('SOURCE_EVENTS_UNAVAILABLE')
            selected.append((min((positions[p['blockId']] for p in proof['segments'] if p['blockId'] in positions),default=10000),batch,source_task,source_revision,acceptance,row,proof,ledger))
    if any(b['source']=='ai' and roots.get(b['blockId'],b['blockId']) not in covered for b in blocks):warnings.add('SOURCE_EVENTS_UNAVAILABLE')
    # Generation origins follow visible source order, then chapter order. Dependencies
    # can retain an earlier clipped source without ever exposing its hidden quotes.
    selected=[item for item in selected if item[1].id not in unavailable_origins]
    origin_order={}
    for position,batch,*_ in selected:origin_order[batch.id]=min(origin_order.get(batch.id,10000),position)
    selected.sort(key=lambda item:(origin_order[item[1].id],item[5].chapter_index))
    sources=[];refs=[];unique=set();source_snapshots={}
    for _,batch,task,source_revision,a,row,proof,ledger in selected:
        if (batch.id,row.content_id) in unique:continue
        unique.add((batch.id,row.content_id))
        sources.append({'runId':batch.id,'runRevision':a.candidate_version,'contentId':row.content_id,'chapterIndex':row.chapter_index,'text':row.text,'style':'server-v1','ledger':deepcopy(ledger),
            'segments':[dict({key:p[key] for key in ('text','sourceStart','sourceEnd','targetStart','targetEnd')},paragraphId=p['blockId'],paragraphHash=p['blockHash']) for p in proof['segments']]})
        refs.append({'generationId':batch.id,'candidateVersion':a.candidate_version,'contentId':row.content_id,'chapterIndex':row.chapter_index,'acceptanceId':a.id,'revisionId':source_revision.id,'sourceBranchId':batch.origin_anchor['branchId'],'sourceHash':digest(row.text),'ledgerHash':digest(canonical(ledger)),'proofHash':digest(canonical(proof))})
        snapshot=source.snapshot(task)
        if snapshot and snapshot.owner_id==owner and snapshot.book_id==batch.book_id and snapshot.branch_id==batch.origin_anchor['branchId'] and snapshot.revision_id==batch.origin_anchor['revisionId']:source_snapshots[batch.id]=snapshot
    try:derived=derive_visible_accepted_events(sources,prefix)
    except (ValueError,KeyError,TypeError) as error:raise Problem('ACCEPTED_BASIS_INVALID','接受事件的引用不闭合，正文仍保留') from error
    if derived['dependencyClippedRefs']:warnings.add('SOURCE_EVENT_DEPENDENCY_OUTSIDE_SCOPE')
    subjects=[]
    for run_id in origin_order:
        used=actor_ids([v['entry'] for v in derived['events'] if v['ref']['runId']==run_id]);snapshot=source_snapshots.get(run_id);bound=set()
        if snapshot:
            for character in snapshot.data.get('characters',[]):
                if character['id'] not in used:continue
                proof=identity_proof(character,snapshot,prefix,roots)
                if not proof:continue
                bound.add(character['id']);subjects.append({'runId':run_id,'subjectId':character['id'],'sourceBranchId':snapshot.branch_id,'snapshotId':snapshot.id,'name':character['displayName'],'primary':proof,'canonicalId':identity_key(anchor['bookId'],proof)})
        if used-bound:warnings.add('SOURCE_IDENTITY_UNPROVEN')
    value={'schema':SCHEMA,'style':'server-v1','scope':deepcopy(anchor),'prefix':[{'blockId':b['blockId'],'textHash':digest(b['text']),'limit':utf16_len(b['text'])} for b in blocks],
        'sources':refs,'events':derived['events'],'subjects':subjects,'state':fold_visible_accepted_events(derived['events'],subjects),'status':'partial' if warnings else 'complete','warnings':sorted(warnings)}
    if len(canonical(value).encode())>12_000_000:raise Problem('CONTEXT_BUDGET_EXCEEDED','已读事件快照超过上限，未调用模型')
    return dict(value,hash=digest(canonical(value)))


def candidate_bindings(basis, snapshot, scope, prefix, roots=None):
    result=[]
    for character in snapshot.data.get('characters',[]):
        proof=identity_proof(character,snapshot,prefix,roots)
        if proof:
            key=identity_key(snapshot.book_id,proof)
            if any(binding['canonicalId']==key for binding in basis['subjects']):result.append({'runId':scope,'subjectId':character['id'],'canonicalId':key})
    return result
