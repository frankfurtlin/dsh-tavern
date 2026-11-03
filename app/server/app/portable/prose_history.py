"""Display-only prose history. No credentials, provider journal or authorizations."""
from copy import deepcopy
from datetime import datetime

from ..domain import digest, utf16_len
from ..errors import Problem
from ..prose.schemas import CreateGeneration
from ..prose.facts import validate_ledger,validate_delta
from ..prose.presentation import validate_ledger as validate_presentations, validate_record as validate_presentation
from ..prose.events import validate_ledger as validate_events_ledger, validate as validate_events
from ..prose.accepted_proof import validate_accepted_proof
from .errors import ArchiveError
from .stream_history import safe_streams,stream,task_objects
from . import accepted_basis

META = frozenset({"targetBranchId","lastAcceptedRevisionId","eventCursor","state","phase","providerState","persistenceState","usageState","cancelRequested","errorCode"})


def capture_received(selected):
    """Freeze newer owner journals once, without altering the source DB rows.

    A failed database mirror must not omit already durable reading/raw bytes
    from a private backup. The restored task is still inert display history.
    """
    from .. import models
    from ..prose import is_imported_history
    from ..prose.stream import overlay
    tasks = {task.id: task for task in selected[models.Task]}
    batches = {batch.id: batch for batch in selected[models.GenerationBatch]}
    copied_tasks = {}
    chapters = []
    for row in selected[models.GenerationChapterVersion]:
        batch = batches[row.generation_id]
        task = tasks[batch.task_id]
        if is_imported_history(task) or row.chapter_index <= batch.accepted_prefix:
            chapters.append(row)
            continue
        value = overlay(batch, task, row)
        if not value:
            if row.durability in ('saved', 'unconfirmed'):
                chapters.append(row)
            else:
                copied = models.GenerationChapterVersion(**{column.key: deepcopy(getattr(row, column.key)) for column in models.GenerationChapterVersion.__table__.columns})
                copied.durability = 'unconfirmed'
                chapters.append(copied)
            continue
        copied = models.GenerationChapterVersion(**{column.key: deepcopy(getattr(row, column.key)) for column in models.GenerationChapterVersion.__table__.columns})
        for key in ('title', 'text', 'streamVersion', 'snapshotVersion', 'completeness', 'durability', 'hints', 'contentHash'):
            attribute = {'streamVersion':'stream_version','snapshotVersion':'snapshot_version','contentHash':'content_hash'}.get(key,key)
            setattr(copied, attribute, deepcopy(value[key]))
        # A portable copy cannot claim that a live/failed mirror was saved to
        # the source database. Its received file is retained as inert history.
        copied.durability = 'unconfirmed'
        chapters.append(copied)
        if task.id not in copied_tasks:
            copied_tasks[task.id] = models.Task(**{column.key: deepcopy(getattr(task, column.key)) for column in models.Task.__table__.columns})
        copied_task = copied_tasks[task.id]
        checkpoint = deepcopy(copied_task.checkpoint)
        for field, ledger_name in (('rawPresentation','prosePresentationLedger'),('factDelta','proseFactLedger'),('eventLedger','proseEventLedger')):
            ledger = checkpoint.setdefault(ledger_name, {})
            entries = ledger.setdefault(str(row.candidate_version), {})
            if field in value:
                entries[str(row.chapter_index)] = deepcopy(value[field])
            else:
                entries.pop(str(row.chapter_index), None)
        copied_task.checkpoint = checkpoint
    selected[models.GenerationChapterVersion] = chapters
    selected[models.Task] = [copied_tasks.get(task.id, task) for task in selected[models.Task]]


def metadata(value):
    if not isinstance(value,dict) or set(value)!=META:raise ArchiveError("BACKUP_GENERATION_HISTORY_INVALID")
    for key,item in value.items():
        if key=="eventCursor":valid=type(item) is int and item>=0
        elif key=="cancelRequested":valid=type(item) is bool
        elif key in {"targetBranchId","lastAcceptedRevisionId","errorCode"}:valid=item is None or isinstance(item,str) and 0<len(item)<=100
        else:valid=isinstance(item,str) and 0<len(item)<=80
        if not valid:raise ArchiveError("BACKUP_GENERATION_HISTORY_INVALID")
    return deepcopy(value)


def safe_checkpoint(task,batch=None,public_streams=None):
    task_objects(task)
    if batch is not None:
        from ..prose.accepted_basis import validate_basis_contract
        try:validate_basis_contract(batch,task)
        except Exception as error:raise ArchiveError('BACKUP_ACCEPTED_BASIS_INVALID') from error
    states=task.checkpoint.get("proseVersionStates",{})
    if not isinstance(states,dict):raise ArchiveError("BACKUP_GENERATION_HISTORY_INVALID")
    output={"proseVersionStates":{}}
    if 'acceptedEventBasis' in task.checkpoint:
        output['acceptedEventBasis']=deepcopy(accepted_basis.validate_shape(task.checkpoint['acceptedEventBasis']))
    source_snapshot=task.checkpoint.get('snapshotId') or task.checkpoint.get('acceptedSourceSnapshotId')
    if batch is not None and source_snapshot:output['acceptedSourceSnapshotId']=source_snapshot
    if 'acceptedCandidateBindings' in task.checkpoint:
        accepted_basis.validate_bindings(task.checkpoint['acceptedCandidateBindings']);output['acceptedCandidateBindings']=deepcopy(task.checkpoint['acceptedCandidateBindings'])
    if 'proseEventLedger' in task.checkpoint:
        try:output['proseEventLedger']=validate_events_ledger(task.checkpoint['proseEventLedger'])
        except (ValueError,TypeError) as error:raise ArchiveError('BACKUP_GENERATION_EVENTS_INVALID') from error
    if 'proseFactLedger' in task.checkpoint:
        try:output['proseFactLedger']=validate_ledger(task.checkpoint['proseFactLedger'])
        except ValueError as error:raise ArchiveError('BACKUP_GENERATION_FACTS_INVALID') from error
    if 'prosePresentationLedger' in task.checkpoint:
        try:output['prosePresentationLedger']=validate_presentations(task.checkpoint['prosePresentationLedger'])
        except (ValueError, TypeError, UnicodeError) as error:raise ArchiveError('BACKUP_GENERATION_PRESENTATION_INVALID') from error
    for version,value in states.items():
        if not isinstance(version,str) or not version.isdecimal() or not 1<=int(version)<=10000:raise ArchiveError("BACKUP_GENERATION_HISTORY_INVALID")
        output["proseVersionStates"][version]=metadata(value)
    if batch is not None:
        current=task.checkpoint.get("portableHistoricalCurrent")
        if current is None:
            current={"targetBranchId":batch.target_branch_id,"lastAcceptedRevisionId":batch.last_accepted_revision_id,
                     "eventCursor":task.event_seq,"state":"result-unknown" if task.error_code=="PROVIDER_STATUS_UNKNOWN" else task.state,
                     "phase":task.result.get("phase","preparing"),"providerState":task.provider_state,
                     "persistenceState":task.persistence_state,"usageState":task.usage_state,
                     "cancelRequested":task.cancel_requested,"errorCode":task.error_code}
        output["portableHistoricalCurrent"]=metadata(current)
    output.update(safe_streams(task) if public_streams is None else deepcopy(public_streams))
    return output if output["proseVersionStates"] or batch is not None or any(key in output for key in ("replyStream","worldStream","proseFactLedger","prosePresentationLedger","proseEventLedger")) else {}


def validate_checkpoint(value):
    if not isinstance(value,dict) or set(value)-{"proseVersionStates","portableHistoricalCurrent","replyStream","worldStream","proseFactLedger","prosePresentationLedger","proseEventLedger","acceptedEventBasis","acceptedCandidateBindings","acceptedSourceSnapshotId"}:raise ArchiveError("BACKUP_GENERATION_HISTORY_INVALID")
    if 'acceptedEventBasis' in value:accepted_basis.validate_shape(value['acceptedEventBasis'],check_hash=False)
    if 'acceptedCandidateBindings' in value:accepted_basis.validate_bindings(value['acceptedCandidateBindings'])
    if 'acceptedSourceSnapshotId' in value and (not isinstance(value['acceptedSourceSnapshotId'],str) or len(value['acceptedSourceSnapshotId'])!=32):accepted_basis.invalid()
    if 'proseEventLedger' in value:
        try:validate_events_ledger(value['proseEventLedger'])
        except (ValueError,TypeError) as error:raise ArchiveError('BACKUP_GENERATION_EVENTS_INVALID') from error
    if 'proseFactLedger' in value:
        try:validate_ledger(value['proseFactLedger'])
        except ValueError as error:raise ArchiveError('BACKUP_GENERATION_FACTS_INVALID') from error
    if 'prosePresentationLedger' in value:
        try:validate_presentations(value['prosePresentationLedger'])
        except (ValueError, TypeError, UnicodeError) as error:raise ArchiveError('BACKUP_GENERATION_PRESENTATION_INVALID') from error
    states=value.get("proseVersionStates",{})
    if not isinstance(states,dict):raise ArchiveError("BACKUP_GENERATION_HISTORY_INVALID")
    for version,item in states.items():
        if not isinstance(version,str) or not version.isdecimal() or not 1<=int(version)<=10000:raise ArchiveError("BACKUP_GENERATION_HISTORY_INVALID")
        metadata(item)
    if "portableHistoricalCurrent" in value:metadata(value["portableHistoricalCurrent"])
    for key in ("replyStream","worldStream"):
        if key in value:stream(value[key],key)


def profile_fields(value):
    identifier=value.get("id") or value.get("profileId")
    if not isinstance(identifier,str) or not identifier or not isinstance(value.get("name"),str) or not isinstance(value.get("model_id"),str) or type(value.get("version")) is not int or value["version"]<1:
        raise ArchiveError("BACKUP_GENERATION_PROFILE_INVALID")
    if len(value["name"])>100 or len(value["model_id"])>200:raise ArchiveError("BACKUP_GENERATION_PROFILE_INVALID")
    return identifier,{key:deepcopy(value[key]) for key in ("name","model_id","version")}


def accepted_manifest_tail(acceptance, batch, revision, result, rows):
    """A proof binds the exact new ordered tail, not any same-text chapter."""
    provenance = revision.provenance
    if (not isinstance(provenance, dict) or provenance.get('type') != 'ai'
            or provenance.get('generationId') != batch.id or provenance.get('taskId') != batch.task_id
            or type(provenance.get('candidateVersion')) is not int
            or provenance['candidateVersion'] != acceptance.candidate_version
            or type(provenance.get('acceptedFrom')) is not int
            or type(provenance.get('acceptedThrough')) is not int
            or not 1 <= provenance['acceptedFrom'] <= provenance['acceptedThrough']
            or provenance['acceptedThrough'] != acceptance.prefix_count):
        raise ValueError('accepted proof provenance')
    first, last = provenance['acceptedFrom'], provenance['acceptedThrough']
    count = last - first + 1
    if (type(result.get('chapterCount')) is not int or result['chapterCount'] != count
            or not isinstance(result.get('chapterIds'), list) or len(result['chapterIds']) != count
            or not isinstance(result.get('acceptedProofs'), list) or len(result['acceptedProofs']) != count
            or len(set(result['chapterIds'])) != count):
        raise ValueError('accepted proof exact prefix count')
    parent = rows['revision'].get(revision.parent_revision_id)
    if parent is None or len(revision.manifest) < count:
        raise ValueError('accepted proof parent')
    if first == 1:
        anchor = batch.origin_anchor
        if parent.id != anchor['revisionId'] or parent.id != batch.request['expectedHeadRevisionId']:
            raise ValueError('accepted proof original parent')
        block = rows['paragraph'][anchor['blockId']]
        from ..domain import utf16_slice
        from ..prose.prefix_fragments import validate_revision_fragments
        if anchor['start'] != anchor['end']:
            raise ValueError('accepted proof paragraph boundary')
        utf16_slice(block.text, 0, anchor['end'])
        aliases = validate_revision_fragments(revision, lambda model, identifier:
            rows['paragraph' if model.__name__ == 'ContentBlock' else 'revision'].get(identifier))
        fragment = None
        if 0 < anchor['end'] < utf16_len(block.text):
            proof = next((p for p in revision.provenance.get('paragraphPrefixes', [])
                if p['prefixSourceBlockId'] == block.id and p['limit'] == anchor['end']), None)
            if proof is None or aliases.get(proof['blockId']) != block.id:
                raise ValueError('accepted proof exact character prefix')
            fragment = proof['blockId']
        elif aliases:
            raise ValueError('accepted proof unexpected character prefix')
        prefix, stopped = [], False
        for chapter in parent.manifest:
            keep = []
            for identifier in chapter['blocks']:
                if identifier == block.id:
                    if anchor['end'] == utf16_len(block.text):keep.append(identifier)
                    elif fragment is not None:keep.append(fragment)
                    stopped = True
                    break
                keep.append(identifier)
            if keep:prefix.append({**chapter, 'blocks':keep})
            if stopped:break
        if not stopped:
            raise ValueError('accepted proof origin membership')
    else:
        if (parent.branch_id != revision.branch_id or not any(
                prior.generation_id == batch.id and prior.prefix_count == first - 1
                and prior.revision_id == parent.id and prior.candidate_version <= acceptance.candidate_version
                for prior in rows.get('generation_acceptance', {}).values())):
            raise ValueError('accepted proof prior accepted prefix')
        prefix = parent.manifest
    tail = revision.manifest[-count:]
    if (revision.manifest[:-count] != prefix or [chapter['id'] for chapter in tail] != result['chapterIds']
            or set(result['chapterIds']) & {chapter['id'] for chapter in parent.manifest}
            or {identifier for chapter in tail for identifier in chapter['blocks']}
               & {identifier for chapter in parent.manifest for identifier in chapter['blocks']}):
        raise ValueError('accepted proof ordered new tail')
    return first, tail


def validate_generation_rows(rows,profiles,contents):
    batches=rows.get("generation_batch",{});versions=rows.get("chapter_version",{})
    branches=rows.get("branch",{});revisions=rows.get("revision",{});tasks=rows.get("task",{})
    grouped={}
    for version in versions.values():grouped.setdefault((version.generation_id,version.candidate_version),[]).append(version)
    for batch in batches.values():
        try: request=CreateGeneration.model_validate({key:value for key,value in batch.request.items() if key!='acceptedBasisSchema'},strict=True)
        except Exception as error:raise ArchiveError("BACKUP_GENERATION_INVALID") from error
        if batch.book_id not in rows.get("book",{}) or batch.origin_anchor.get("bookId")!=batch.book_id or request.originAnchor.model_dump()!=batch.origin_anchor:
            raise ArchiveError("BACKUP_GENERATION_INVALID")
        source=revisions.get(request.expectedHeadRevisionId)
        if not source or source.branch_id!=batch.origin_anchor.get("branchId"):
            raise ArchiveError("BACKUP_GENERATION_INVALID")
        task=tasks.get(batch.task_id)
        if not task or task.payload.get("protocol")!="reader-prose-v1" or task.payload.get("bookId")!=batch.book_id:
            raise ArchiveError("BACKUP_GENERATION_INVALID")
        ledger=task.checkpoint.get('proseFactLedger',{})
        events=task.checkpoint.get('proseEventLedger',{})
        subjects={person['id'] for kind in ('knowledge_snapshot','analysis_chunk') for row in rows.get(kind,{}).values()
            if row.branch_id == batch.origin_anchor.get('branchId') for person in row.data.get('characters',[])}
        if (any(number not in batch.version_times for number in events)
                or any(int(index)>request.chapterCount for entries in events.values() for index in entries)):
            raise ArchiveError('BACKUP_GENERATION_EVENTS_INVALID')
        if any(str(number) not in batch.version_times for number in ledger):raise ArchiveError('BACKUP_GENERATION_FACTS_INVALID')
        if any(int(index)>request.chapterCount for entries in ledger.values() for index in entries):raise ArchiveError('BACKUP_GENERATION_FACTS_INVALID')
        presentations = task.checkpoint.get('prosePresentationLedger', {})
        if (any(number not in batch.version_times for number in presentations)
                or any(int(index)>request.chapterCount for entries in presentations.values() for index in entries)):
            raise ArchiveError('BACKUP_GENERATION_PRESENTATION_INVALID')
        profile=batch.profile_snapshot
        if set(profile)!={"profileId","name","model_id","version","historical"} or profile["historical"] is not True or request.profileId!=profile["profileId"] or profile["profileId"] not in profiles:
            raise ArchiveError("BACKUP_GENERATION_PROFILE_INVALID")
        if not 1<=batch.candidate_version<=10000 or not 0<=batch.accepted_prefix<=request.chapterCount or set(batch.version_times)!= {str(index) for index in range(1,batch.candidate_version+1)}:
            raise ArchiveError("BACKUP_GENERATION_INVALID")
        for value in batch.version_times.values():
            try: valid=isinstance(value,str) and datetime.fromisoformat(value).tzinfo is not None
            except ValueError:valid=False
            if not valid:raise ArchiveError("BACKUP_GENERATION_INVALID")
        if batch.target_branch_id is not None and (batch.target_branch_id not in branches or branches[batch.target_branch_id].book_id!=batch.book_id):raise ArchiveError("BACKUP_GENERATION_INVALID")
        if batch.last_accepted_revision_id is not None and (batch.last_accepted_revision_id not in revisions or revisions[batch.last_accepted_revision_id].branch_id!=batch.target_branch_id):raise ArchiveError("BACKUP_GENERATION_INVALID")
        if batch.accepted_prefix and (not batch.target_branch_id or not batch.last_accepted_revision_id):raise ArchiveError("BACKUP_GENERATION_INVALID")
        for number in range(1,batch.candidate_version+1):
            selected=grouped.get((batch.id,number),[])
            if sorted(row.chapter_index for row in selected)!=list(range(1,request.chapterCount+1)):raise ArchiveError("BACKUP_GENERATION_INVALID")
            for chapter in selected:
                # Stream versions count text replacement epochs; snapshots count
                # durable flushes within those epochs. Both counters are valid
                # independently, and many snapshots can share one stream version.
                if contents.get(chapter.content_id)!={"generationId":batch.id,"chapterIndex":chapter.chapter_index} or chapter.stream_version<1 or chapter.snapshot_version<0 or chapter.completeness not in {"partial","complete"} or chapter.durability not in {"unconfirmed","saved"}:
                    raise ArchiveError("BACKUP_GENERATION_INVALID")
                if chapter.content_hash is not None and chapter.content_hash!=digest(chapter.text):raise ArchiveError("BACKUP_BODY_HASH_MISMATCH")
                if chapter.completeness=="complete" and chapter.durability=="saved" and chapter.content_hash!=digest(chapter.text):raise ArchiveError("BACKUP_BODY_HASH_MISMATCH")
                delta=ledger.get(str(number),{}).get(str(chapter.chapter_index))
                if delta is not None:
                    try:
                        validate_delta(delta,chapter.text)
                        if any(entry['subjectId'] is not None and entry['subjectId'] not in subjects for entry in delta['entries']):
                            raise ValueError('fact subject closure')
                    except ValueError as error:raise ArchiveError('BACKUP_GENERATION_FACTS_INVALID') from error
                event_ledger=events.get(str(number),{}).get(str(chapter.chapter_index))
                if event_ledger is not None:
                    try:validate_events(event_ledger, chapter.text, subjects=subjects)
                    except (ValueError,TypeError) as error:raise ArchiveError('BACKUP_GENERATION_EVENTS_INVALID') from error
                presentation = presentations.get(str(number), {}).get(str(chapter.chapter_index))
                if presentation is not None:
                    try:
                        validate_presentation(presentation, chapter.text, chapter.title)
                        selected = next(a for a in presentation['attempts'] if a['kind'] == presentation['selected'])
                        if chapter.completeness == 'complete' and (selected['finishReason'] not in ('stop', 'completed')
                                or presentation['presentation']['pending'] or not chapter.text.strip()
                                or presentation['presentation']['format'] == 'unrecognized'):
                            raise ValueError('incomplete received chapter')
                    except (ValueError, TypeError, UnicodeError) as error:raise ArchiveError('BACKUP_GENERATION_PRESENTATION_INVALID') from error
        batch.profile_snapshot={"id":profile["profileId"],**{key:profile[key] for key in ("name","model_id","version")},"historical":True}
    try:
        from ..prose.selection_generation import validate_historical
        validate_historical(rows)
    except (ValueError, KeyError, TypeError, Problem) as error:
        raise ArchiveError('BACKUP_GENERATION_SELECTION_INVALID') from error
    for acceptance in rows.get("generation_acceptance",{}).values():
        batch=batches.get(acceptance.generation_id);revision=revisions.get(acceptance.revision_id)
        if not batch or not revision or branches[revision.branch_id].book_id!=batch.book_id or str(acceptance.candidate_version) not in batch.version_times or not 1<=acceptance.prefix_count<=batch.request["chapterCount"]:
            raise ArchiveError("BACKUP_GENERATION_ACCEPTANCE_INVALID")
        result=acceptance.result
        if result.get("generationId")!=batch.id or result.get("candidateId")!=batch.id or result.get("revisionId")!=revision.id or result.get("branchId")!=revision.branch_id or result.get("candidateVersion")!=acceptance.candidate_version or result.get("acceptedPrefix")!=acceptance.prefix_count:
            raise ArchiveError("BACKUP_GENERATION_ACCEPTANCE_INVALID")
        markers = [result.get('acceptedProofSchema'), revision.provenance.get('acceptedProofSchema')]
        if any(value is not None for value in markers) and (markers != ['accepted-content-proof-v1'] * 2 or 'acceptedProofs' not in result):
            raise ArchiveError('BACKUP_GENERATION_ACCEPTED_PROOF_INVALID')
        if 'acceptedProofs' in result:
            try:
                accepted_from, tail = accepted_manifest_tail(acceptance, batch, revision, result, rows)
                for index, (proof, manifest) in enumerate(zip(result['acceptedProofs'], tail), accepted_from):
                    chapter = next(row for row in grouped[(batch.id, acceptance.candidate_version)]
                                   if row.chapter_index == index)
                    chapter_id = manifest['id']
                    if chapter.completeness != 'complete' or chapter.durability != 'saved' or chapter.content_hash != digest(chapter.text):
                        raise ValueError('accepted proof complete source')
                    validate_accepted_proof(proof, chapter.text, chapter.content_id,
                                           index, chapter_id, manifest['blocks'])
                    for part in proof['segments']:
                        block = rows['paragraph'][part['blockId']]
                        if (block.book_id != batch.book_id or block.chapter_id != chapter_id
                                or block.text != part['text'] or block.content_hash != part['blockHash']):
                            raise ValueError('accepted proof actual paragraph')
            except (ValueError, TypeError, KeyError, StopIteration) as error:
                raise ArchiveError('BACKUP_GENERATION_ACCEPTED_PROOF_INVALID') from error
    for version in versions.values():
        if version.generation_id not in batches or not 1<=version.candidate_version<=batches[version.generation_id].candidate_version:raise ArchiveError("BACKUP_GENERATION_INVALID")
    accepted_basis.validate_rows(rows)
