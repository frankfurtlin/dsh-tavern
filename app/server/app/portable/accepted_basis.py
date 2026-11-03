"""Declared references for an inert, frozen accepted-story basis."""
from ..domain import canonical,digest
from .errors import ArchiveError


def invalid():raise ArchiveError('BACKUP_ACCEPTED_BASIS_INVALID')


def validate_shape(value,*,check_hash=True):
    if (not isinstance(value,dict) or set(value)!={'schema','style','scope','prefix','sources','events','subjects','state','status','warnings','hash'}
            or value['schema']!='accepted-events-basis-v1' or value['style']!='server-v1'
            or value['status'] not in ('partial','complete') or not isinstance(value['scope'],dict)
            or not isinstance(value['state'],dict) or value['state'].get('schema')!='accepted-event-state-v1'):
        invalid()
    for key,limit in [('prefix',10000),('sources',1000),('events',12000),('subjects',2000),('warnings',3)]:
        if not isinstance(value[key],list) or len(value[key])>limit:invalid()
    if (any(not isinstance(warning,str) for warning in value['warnings'])
            or value['warnings']!=sorted(set(value['warnings'])) or set(value['warnings'])-{'SOURCE_EVENTS_UNAVAILABLE','SOURCE_EVENT_DEPENDENCY_OUTSIDE_SCOPE','SOURCE_IDENTITY_UNPROVEN'}
            or value['status']!=('partial' if value['warnings'] else 'complete')
            or check_hash and value['hash']!=digest(canonical({key:item for key,item in value.items() if key!='hash'}))):invalid()
    return value


def references(value):
    """Only these paths may change. Event IDs, names, raw strings and quotes stay literal."""
    validate_shape(value,check_hash=False)
    output=[]
    def add(path,kind,identifier):
        if not isinstance(identifier,str) or not identifier or len(identifier)>100:invalid()
        output.append((path,kind,identifier))
    for key,kind in [('bookId','book'),('branchId','branch'),('revisionId','revision'),('chapterId','chapter'),('blockId','paragraph')]:add('/scope/'+key,kind,value['scope'].get(key))
    for i,p in enumerate(value['prefix']):
        if not isinstance(p,dict) or set(p)!={'blockId','textHash','limit'}:invalid()
        add(f'/prefix/{i}/blockId','paragraph',p['blockId'])
    for i,s in enumerate(value['sources']):
        if not isinstance(s,dict) or set(s)!={'generationId','candidateVersion','contentId','chapterIndex','acceptanceId','revisionId','sourceBranchId','sourceHash','ledgerHash','proofHash'}:invalid()
        for key,kind in [('generationId','generation_batch'),('contentId','chapter_content'),('acceptanceId','generation_acceptance'),('revisionId','revision'),('sourceBranchId','branch')]:add(f'/sources/{i}/{key}',kind,s[key])
    def event_ref(path,ref):
        if not isinstance(ref,dict) or set(ref)!={'runId','contentId','localEventId'}:invalid()
        add(path+'/runId','generation_batch',ref['runId']);add(path+'/contentId','chapter_content',ref['contentId'])
    for i,e in enumerate(value['events']):
        if not isinstance(e,dict) or set(e)!={'ref','sourceRunRevision','sourceHash','ledgerHash','entry','dependencies','frameRef','temporalFrame','evidence'}:invalid()
        event_ref(f'/events/{i}/ref',e['ref'])
        for j,ref in enumerate(e['dependencies']):event_ref(f'/events/{i}/dependencies/{j}',ref)
        if e['frameRef'] is not None:event_ref(f'/events/{i}/frameRef',e['frameRef'])
        for j,p in enumerate(e['evidence']):add(f'/events/{i}/evidence/{j}/paragraphId','paragraph',p['paragraphId'])
    for i,s in enumerate(value['subjects']):
        if not isinstance(s,dict) or set(s)!={'runId','subjectId','sourceBranchId','snapshotId','name','primary','canonicalId'}:invalid()
        for key,kind in [('runId','generation_batch'),('sourceBranchId','branch'),('snapshotId','knowledge_snapshot')]:add(f'/subjects/{i}/{key}',kind,s[key])
        add(f'/subjects/{i}/primary/blockId','paragraph',s['primary']['blockId'])
    return output


def validate_bindings(value):
    if not isinstance(value,list) or len(value)>2000 or any(not isinstance(b,dict) or set(b)!={'runId','subjectId','canonicalId'} or any(not isinstance(b[k],str) or not b[k] for k in b) for b in value):invalid()


def validate_rows(rows,*,rebase=False):
    from ..prose.accepted_basis import collect_basis_from_rows,candidate_bindings,_RowSources,validate_basis_contract,revision_roots
    from ..models import ContentBlock,Revision
    for batch in rows.get('generation_batch',{}).values():
        task=rows['task'][batch.task_id];basis=task.checkpoint.get('acceptedEventBasis')
        try:
            validate_basis_contract(batch,task,check_hash=not rebase)
            if basis is None:continue
            validate_shape(basis,check_hash=not rebase)
            if basis['scope']!=batch.origin_anchor:invalid()
            expected=collect_basis_from_rows(rows,batch.origin_anchor,rebasing=rebase)
            if not rebase and expected!=basis:invalid()
            checkpoint=dict(task.checkpoint,acceptedEventBasis=expected)
            if 'acceptedCandidateBindings' in checkpoint:
                source=_RowSources(rows);snapshot=source.snapshot(task)
                if snapshot is None or snapshot.branch_id!=batch.origin_anchor['branchId'] or snapshot.revision_id!=batch.origin_anchor['revisionId']:invalid()
                roots=revision_roots(source,source.get(Revision,batch.origin_anchor['revisionId']),batch.book_id)
                prefix=[]
                for ref in expected['prefix']:
                    from ..domain import utf16_slice
                    block=source.get(ContentBlock,ref['blockId']);prefix.append({'sourceParagraphId':roots.get(block.id,block.id),'text':utf16_slice(block.text,0,ref['limit'])})
                bindings=candidate_bindings(expected,snapshot,batch.id,prefix,roots)
                if not rebase and checkpoint['acceptedCandidateBindings']!=bindings:invalid()
                checkpoint['acceptedCandidateBindings']=bindings
            if rebase:task.checkpoint=checkpoint
        except ArchiveError:raise
        except Exception as error:raise ArchiveError('BACKUP_ACCEPTED_BASIS_INVALID') from error
