"""Real PostgreSQL completion/CAS contracts, with every provider forbidden."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func, text, inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from alembic import command
from alembic.config import Config
from app.config import settings
from app.db import SessionLocal, engine
from app.models import UserSettings, Task, UsageRecord, PlaybackPosition, Progress, Branch, Revision, Book, uid
from app.providers import MockAdapter, ChatCompletionsAdapter
from .helpers import BASE, read_chapter
from app.domain import canonical, digest, make_anchor, utf16_len


@pytest.fixture(autouse=True)
def no_provider(reset_db, monkeypatch, tmp_path):
    assert engine.url.database == 'reader_test' and settings.ai_mode == 'mock'
    monkeypatch.setattr(settings, 'storage_path', tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Cloud preferences/positions must not invoke a provider')
    for adapter in (MockAdapter, ChatCompletionsAdapter):
        monkeypatch.setattr(adapter, 'generate', forbidden)
        monkeypatch.setattr(adapter, 'generate_stream', forbidden)


def reader_body(client, demo, block_index=0, **extra):
    block = read_chapter(client, demo)['blocks'][block_index]
    context = {'storyId': 'book-' + demo['bookId'], 'mode': 'read', 'perspective': 'reader',
        'entryOrigin': 'reader', 'bookId': demo['bookId'], 'readerBranchId': demo['branchId'],
        'anchor': block['startAnchor']}
    return {'expectedVersion': 0, 'context': context, 'kind': 'reader', 'mode': 'read',
        'lineId': demo['branchId'], 'contentRevision': demo['revisionId'], 'paragraphId': block['id'],
        'lastCompletedAnchor': block['endAnchor'], 'completed': True, 'ended': False, **extra}


def get_position(client, body):
    return client.get(BASE + '/playback/position', params={k: body['context']['storyId'] if k == 'storyId' else body[k]
        for k in ('storyId', 'kind', 'mode', 'lineId', 'contentRevision')})


def put_position(client, body, key='completion'):
    return client.put(BASE + '/playback/position', json=body, headers={'Idempotency-Key': key})


def test_reading_settings_cloud_endpoint_has_real_cas_and_preserves_legacy_fields(client, reader):
    response = client.get(BASE + '/settings/reading')
    assert response.status_code == 200, response.text
    current = response.json()
    assert current['editVersion'] == current['version'] == 1
    assert current['speech'] == {'rate': 1, 'voiceId': '', 'sleepMinutes': 0, 'stopAtChapterEnd': False}
    wanted = {'rate': 1.25, 'voiceId': 'device-voice-zh', 'sleepMinutes': 15, 'stopAtChapterEnd': True}
    saved = client.patch(BASE + '/settings/reading', json={'expectedVersion': 1, 'speech': wanted})
    assert saved.status_code == 200, saved.text
    assert saved.json()['speech'] == wanted and saved.json()['editVersion'] == 2
    stale = client.patch(BASE + '/settings/reading', json={'expectedVersion': 1, 'speech': {**wanted, 'rate': 2}})
    assert stale.status_code == 409 and stale.json()['code'] == 'SETTINGS_CONFLICT'
    legacy = client.put(BASE + '/settings', json={'expectedVersion': 2, 'fontSize': 22, 'lineHeight': 1.8, 'theme': 'dark'})
    assert legacy.status_code == 200
    assert client.get(BASE + '/settings/reading').json()['speech'] == wanted


def test_actual_completed_paragraph_is_durable_without_changing_read_position_or_calling_model(client, reader, demo):
    body = reader_body(client, demo)
    empty = get_position(client, body)
    assert empty.status_code == 200 and empty.json() == {'version': 0, 'position': None}, empty.text
    assert client.get(BASE + '/playback/capabilities').json() == {'ready': True, 'protocol': 'reader-playback-v1'}
    saved = put_position(client, body)
    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result['version'] == 1 and result['readAdvance'] == 'not-requested'
    assert result['position']['lastCompletedAnchor'] == body['lastCompletedAnchor']
    assert result['position']['maxHeardAnchor'] == body['lastCompletedAnchor']
    assert get_position(client, body).json()['position'] == result['position']
    with SessionLocal() as db:
        from app.models import Progress
        assert db.scalar(select(func.count()).select_from(Progress)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_unknown_completion_receipt_lookup_and_replay_are_pure_and_exactly_once(client, reader, demo):
    body = reader_body(client, demo)
    query = {'requestId': 'unknown-completion', 'storyId': body['context']['storyId']}
    absent = client.get(BASE + '/playback/request-status', params=query)
    assert absent.status_code == 200 and absent.json() == {'found': False}, absent.text
    saved = put_position(client, body, query['requestId'])
    assert saved.status_code == 200, saved.text
    receipt = client.get(BASE + '/playback/request-status', params=query)
    assert receipt.status_code == 200 and receipt.json() == {'found': True, 'receipt': saved.json()}, receipt.text
    assert put_position(client, body, query['requestId']).json() == saved.json()
    assert get_position(client, body).json()['version'] == 1


@pytest.mark.parametrize('change', [
    {'rate': True}, {'rate': .74}, {'rate': 2.01}, {'voiceId': 'x' * 501},
    {'sleepMinutes': True}, {'sleepMinutes': 10}, {'stopAtChapterEnd': 1}])
def test_speech_preferences_strict_values_never_change_saved_version(client, reader, change):
    before = client.get(BASE + '/settings/reading').json()
    speech = {**before['speech'], **change}
    bad = client.patch(BASE + '/settings/reading', json={'expectedVersion': 1, 'speech': speech})
    assert bad.status_code == 422, bad.text
    assert client.get(BASE + '/settings/reading').json() == before


def test_completion_and_progress_are_one_cas_transaction_preserving_anchor_and_monotonic_boundaries(client, reader, demo):
    early = reader_body(client, demo)
    later = reader_body(client, demo, 1, expectedProgressVersion=0)
    later['context'] = early['context']
    first = put_position(client, later, 'advance-completed')
    assert first.status_code == 200, first.text
    assert first.json()['readAdvance'] == 'advanced'
    p = first.json()['readerProgress']
    assert p == {'version': 1, 'anchor': early['context']['anchor'],
        'maxObservedAnchor': later['lastCompletedAnchor'], 'revisionId': demo['revisionId']}
    stale_progress = put_position(client, {**early, 'expectedVersion': 1, 'expectedProgressVersion': 0}, 'stale-progress')
    assert stale_progress.status_code == 409 and stale_progress.json()['code'] == 'PLAYBACK_PROGRESS_CONFLICT'
    assert get_position(client, early).json()['version'] == 1
    with SessionLocal() as db:
        assert db.scalar(select(Progress)).version == 1
    older = put_position(client, {**early, 'expectedVersion': 1, 'expectedProgressVersion': 1}, 'older-completion')
    assert older.status_code == 200 and older.json()['readAdvance'] == 'already-observed', older.text
    assert older.json()['position']['lastCompletedAnchor'] == early['lastCompletedAnchor']
    assert older.json()['position']['maxHeardAnchor'] == later['lastCompletedAnchor']
    assert older.json()['readerProgress'] == p
    position_conflict = put_position(client, {**later, 'expectedProgressVersion': 1}, 'stale-position')
    assert position_conflict.status_code == 409 and position_conflict.json()['code'] == 'PLAYBACK_CONFLICT'
    assert get_position(client, early).json()['version'] == 2
    assert put_position(client, later, 'advance-completed').json() == first.json()
    assert client.get(BASE + '/playback/request-status', params={'requestId': 'stale-progress',
        'storyId': early['context']['storyId']}).json() == {'found': False}


@pytest.mark.parametrize('same_key', [False, True])
def test_two_actual_connections_cannot_double_save_or_advance_progress(client, reader, demo, same_key):
    body = reader_body(client, demo, expectedProgressVersion=0)
    barrier = threading.Barrier(2)
    def send(key):
        barrier.wait(timeout=5)
        return put_position(client, body, key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(send, 'device-a'); b = pool.submit(send, 'device-a' if same_key else 'device-b')
        outcomes = [a.result(timeout=10), b.result(timeout=10)]
    assert sorted(r.status_code for r in outcomes) == ([200, 200] if same_key else [200, 409])
    if same_key: assert outcomes[0].json() == outcomes[1].json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PlaybackPosition)) == 1
        assert db.scalar(select(PlaybackPosition)).version == 1
        assert db.scalar(select(Progress)).version == 1


@pytest.mark.parametrize('mutation', ['not-completed', 'partial', 'wrong-paragraph', 'wrong-revision', 'bool-offset', 'wrong-mode'])
def test_invalid_completion_is_rejected_without_position_progress_or_receipt(client, reader, demo, mutation):
    body = reader_body(client, demo, expectedProgressVersion=0)
    if mutation == 'not-completed': body['completed'] = False
    elif mutation == 'partial': body['lastCompletedAnchor'] = body['context']['anchor']
    elif mutation == 'wrong-paragraph': body['paragraphId'] = uid()
    elif mutation == 'wrong-revision': body['contentRevision'] = uid()
    elif mutation == 'bool-offset': body['lastCompletedAnchor']['end'] = True
    else: body['mode'] = 'draft'
    bad = put_position(client, body, 'invalid-completion')
    assert bad.status_code in (404, 422), bad.text
    with SessionLocal() as db:
        from app.models import Idempotency
        assert db.scalar(select(func.count()).select_from(PlaybackPosition)) == 0
        assert db.scalar(select(func.count()).select_from(Progress)) == 0
        assert db.scalar(select(func.count()).select_from(Idempotency).where(Idempotency.scope == 'playback-save')) == 0


def test_owner_and_world_publication_family_are_authorized_even_for_receipt_replay(client, reader, demo):
    from app.world.models import World
    from .test_book_deletion import legacy_publication
    from .test_world_security import _login_other
    owner = reader['user']['id']
    with SessionLocal() as db:
        w1 = World(id=uid(), owner_id=owner, name='自编世界一', source_title='合成来源', source={}, source_book_id=demo['bookId'], definition={}, status='active')
        w2 = World(id=uid(), owner_id=owner, name='自编世界二', source_title='合成来源', source={}, source_book_id=demo['bookId'], definition={}, status='active')
        db.add_all([w1, w2]); db.flush()
        b1, r1, block1, a1 = legacy_publication(db, owner, demo, w1.id)
        b2, r2, block2, a2 = legacy_publication(db, owner, demo, w2.id)
        db.commit(); worlds = (w1.id, w2.id); pub = {'bookId':demo['bookId'], 'branchId':b1.id, 'revisionId':r1.id}
        foreign = {'bookId':demo['bookId'], 'branchId':b2.id, 'revisionId':r2.id}
    body = reader_body(client, pub, 0)
    body['context']['storyId'] = 'world-' + worlds[0]
    assert put_position(client, body).status_code == 200
    wrong = reader_body(client, foreign)
    wrong['context']['storyId'] = body['context']['storyId']
    assert put_position(client, wrong, 'wrong-family').status_code == 404
    assert get_position(client, wrong).status_code == 404
    source = reader_body(client, demo); source['context']['storyId'] = body['context']['storyId']
    assert put_position(client, source, 'plain-source-not-publication').status_code == 404
    token = client.headers['Authorization']; _login_other(client)
    try:
        assert put_position(client, body).status_code == 404
        assert get_position(client, body).status_code == 404
        assert client.get(BASE+'/playback/request-status',params={'requestId':'completion', 'storyId':body['context']['storyId']}).status_code == 404
    finally: client.headers['Authorization'] = token


def synthetic_world(owner):
    from app.world.models import World, WorldCheckpoint, WorldLine
    from app.world.state import initial_state, checkpoint_hash
    from app.world.templates import template_definition
    from app.world.schemas import WorldRules
    definition = template_definition('original-mystery-v1', WorldRules().model_dump())
    source = {'kind':'standalone', 'anchor':None, 'blocks':[], 'chapters':[]}
    actor = next(e['id'] for e in definition['entities'] if e['type'] == 'character')
    definition, state = initial_state(definition, actor)
    with SessionLocal() as db:
        world = World(id=uid(), owner_id=owner, name='自编公开听书世界', source_title='原创合成模板', source=source, definition=definition, status='active')
        line = WorldLine(id=uid(), owner_id=owner, world_id=world.id, name='原线', head_checkpoint_id=uid())
        db.add(world); db.flush(); db.add(line); db.flush()
        story = '公开第一段🙂。\n\n公开第二段。'
        cp = WorldCheckpoint(id=line.head_checkpoint_id, owner_id=owner, world_id=world.id, line_id=line.id,
            sequence=0, state=state, definition=definition, text=story, title='合成公开场景', kind='identity',
            action='', options=[], changes=[], summary='', content_hash=checkpoint_hash(state, definition, story, None))
        db.add(cp); world.active_line_id = line.id; db.commit()
        body = {'expectedVersion': 0, 'context': {'storyId':'world-'+world.id,'mode':'read','perspective':'reader',
            'entryOrigin':'reader','worldId':world.id,'worldLineId':line.id,'checkpointId':cp.id},
            'kind':'world','mode':'read','lineId':line.id,'contentRevision':cp.id,'paragraphId':cp.id+':0',
            'lastCompletedAnchor':{'worldId':world.id,'worldLineId':line.id,'checkpointId':cp.id,
                'paragraph':0,'start':0,'end':utf16_len(story.split('\n\n')[0]),'offsetUnit':'utf16'},'completed':True,'ended':False}
        return body, deepcopy(cp.state), cp.content_hash


def test_world_public_text_completed_position_never_changes_head_state_camera_or_reading_position(client, reader):
    from app.world.models import WorldCheckpoint, WorldLine
    body, before, hash_before = synthetic_world(reader['user']['id'])
    response = put_position(client, body)
    assert response.status_code == 200, response.text
    assert get_position(client, body).json()['position'] == response.json()['position']
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, body['contentRevision']); line = db.get(WorldLine, body['lineId'])
        assert cp.state == before and cp.content_hash == hash_before
        assert line.head_checkpoint_id == cp.id and line.version == 1 and not line.reading_position
        assert db.scalar(select(func.count()).select_from(Progress)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == db.scalar(select(func.count()).select_from(UsageRecord)) == 0
    assert put_position(client, {**body, 'expectedVersion':1, 'expectedProgressVersion':0}, 'world-cannot-unlock').status_code == 422
    bad = deepcopy(body); bad['expectedVersion']=1;bad['lastCompletedAnchor']['end']-=1
    assert put_position(client, bad, 'world-mid-paragraph').status_code == 422


def test_world_line_deletion_purges_position_and_revokes_old_receipt_without_touching_surviving_line(client, reader):
    from app.world.models import WorldLine
    body, _, _ = synthetic_world(reader['user']['id'])
    assert put_position(client, body).status_code == 200
    world_id = body['context']['worldId']
    with SessionLocal() as db:
        survivor=WorldLine(id=uid(),owner_id=reader['user']['id'],world_id=world_id,name='保留共享历史线',
            head_checkpoint_id=body['contentRevision'],parent_line_id=body['lineId'],fork_checkpoint_id=body['contentRevision'])
        db.add(survivor);db.commit();survivor_id=survivor.id
    shared=deepcopy(body);shared['lineId']=survivor_id;shared['context']['worldLineId']=survivor_id
    shared['lastCompletedAnchor']['worldLineId']=survivor_id
    assert put_position(client,shared,'survivor-completion').status_code==200
    response = client.delete('/api/world/v1/worlds/'+world_id+'/lines/'+body['lineId'])
    assert response.status_code == 200, response.text
    assert get_position(client, body).status_code == 404
    assert put_position(client, body).status_code == 404
    assert client.get(BASE+'/playback/request-status',params={'requestId':'completion','storyId':body['context']['storyId']}).json() == {'found':False}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PlaybackPosition)) == 1
        assert db.get(WorldLine, body['lineId']).status == 'deleted'
        assert db.get(WorldLine,survivor_id).status=='active'
    assert get_position(client,shared).status_code==200
    removed=client.delete('/api/world/v1/worlds/'+world_id)
    assert removed.status_code==200,removed.text
    assert get_position(client,shared).status_code==404
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(PlaybackPosition))==0


def test_book_delete_detaches_publication_playback_but_source_and_deleted_receipts_are_not_replayed(client, reader, demo):
    from .test_book_deletion import legacy_publication, delete
    with SessionLocal() as db:
        branch, revision, _, _ = legacy_publication(db, reader['user']['id'], demo, uid())
        db.commit(); published={'bookId':demo['bookId'],'branchId':branch.id,'revisionId':revision.id}
    body = reader_body(client, published, 0)
    source = reader_body(client, demo)
    assert put_position(client, body, 'publication-position').status_code == 200
    assert put_position(client, source, 'source-position').status_code == 200
    gone = delete(client, demo['bookId']); assert gone.status_code == 202, gone.text
    carrier = gone.json()['retainedStories'][0]['bookId']
    fresh = deepcopy(body); fresh['context']['storyId']='book-'+carrier;fresh['context']['bookId']=carrier
    fresh['context']['anchor']['bookId']=carrier;fresh['lastCompletedAnchor']['bookId']=carrier
    restored = get_position(client, fresh)
    assert restored.status_code == 200, restored.text
    assert restored.json()['version'] == 2 and restored.json()['position']['lastCompletedAnchor'] == fresh['lastCompletedAnchor']
    assert get_position(client, source).status_code == 404 and put_position(client, body, 'publication-position').status_code == 404
    with SessionLocal() as db:
        rows=list(db.scalars(select(PlaybackPosition)));assert len(rows)==1 and rows[0].book_id==carrier


def test_0012_old_settings_defaults_and_empty_down_up_preserve_source_and_preferences_cas(client, reader, demo):
    from app.models import SourceDocument
    config=Config('alembic.ini')
    with SessionLocal() as db:
        source=db.get(SourceDocument, db.get(Book,demo['bookId']).source_id)
        original=(source.id,deepcopy(source.source_map),db.get(Revision,demo['revisionId']).content_hash)
    try:
        command.downgrade(config,'0011')
        assert 'playback_positions' not in inspect(engine).get_table_names()
        assert client.get(BASE+'/playback/capabilities').json()=={'ready':False,'protocol':'reader-playback-v1'}
        assert client.get('/health/ready').status_code==503
        with engine.begin() as db:
            db.execute(text("UPDATE user_settings SET font_size=24, theme='dark' WHERE owner_id=:id"),{'id':reader['user']['id']})
        command.upgrade(config,'head')
        new=client.get(BASE+'/settings/reading');assert new.status_code==200,new.text
        assert new.json()['fontSize']==24 and new.json()['theme']=='dark' and new.json()['editVersion']==1
        assert new.json()['speech']=={'rate':1,'voiceId':'','sleepMinutes':0,'stopAtChapterEnd':False}
        assert client.get('/health/ready').status_code==200
        with SessionLocal() as db:
            source=db.get(SourceDocument,original[0])
            assert (source.id,source.source_map,db.get(Revision,demo['revisionId']).content_hash)==original
            assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0022'
    finally: command.upgrade(config,'head')


@pytest.mark.parametrize('populated', ['position','preferences'])
def test_0012_downgrade_refuses_loss_before_ddl(client, reader, demo, populated):
    if populated=='position':assert put_position(client,reader_body(client,demo)).status_code==200
    else:
        assert client.patch(BASE+'/settings/reading',json={'expectedVersion':1,'speech':
            {'rate':1.5,'voiceId':'fixture','sleepMinutes':5,'stopAtChapterEnd':True}}).status_code==200
    before=client.get(BASE+'/settings/reading').json()
    with pytest.raises(RuntimeError,match='Cannot downgrade'):
        command.downgrade(Config('alembic.ini'),'0011')
    assert client.get(BASE+'/settings/reading').json()==before
    assert client.get('/health/ready').status_code==200
    assert 'playback_positions' in inspect(engine).get_table_names()


def test_commit_failure_rolls_back_both_progress_and_position_then_same_key_can_retry(client, reader, demo, monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import OperationalError
    body=reader_body(client,demo,expectedProgressVersion=0)
    original=Session.commit; failed=[]
    def once(db):
        if not failed and any(isinstance(row,PlaybackPosition) for row in db.new):
            failed.append(True);raise OperationalError('synthetic playback commit failure',{},Exception('own fixture'))
        return original(db)
    monkeypatch.setattr(Session,'commit',once)
    first=put_position(client,body,'commit-retry')
    assert first.status_code==503,first.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PlaybackPosition))==0
        assert db.scalar(select(func.count()).select_from(Progress))==0
    retry=put_position(client,body,'commit-retry')
    assert retry.status_code==200 and retry.json()['version']==retry.json()['readerProgress']['version']==1,retry.text


def test_initial_progress_never_uses_unplayed_future_context_as_observed_range(client, reader, demo):
    earlier=reader_body(client,demo,expectedProgressVersion=0)
    later=reader_body(client,demo,1)
    earlier['context']=later['context']
    response=put_position(client,earlier,'future-current-anchor')
    assert response.status_code==422 and response.json()['code']=='PLAYBACK_ANCHOR_INVALID',response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Progress))==0
        assert db.scalar(select(func.count()).select_from(PlaybackPosition))==0


@pytest.mark.parametrize('imported', [False, True])
def test_complete_saved_draft_playback_is_version_scoped_and_cannot_unlock_formal_reading(client, reader, demo, imported):
    from app.models import GenerationBatch, GenerationChapterVersion, TaskEvent
    base=reader_body(client,demo)
    with SessionLocal() as db:
        task=Task(id=uid(),owner_id=reader['user']['id'],task_type='story_flow',state='succeeded',
            payload={'bookId':demo['bookId'],'protocol':'reader-prose-v1','importedHistory':imported},
            result={'phase':'candidate_ready'},checkpoint={},persistence_state='saved')
        db.add(task);db.flush()
        batch=GenerationBatch(id=uid(),owner_id=task.owner_id,task_id=task.id,book_id=demo['bookId'],
            origin_anchor=base['context']['anchor'],request={'chapterCount':1},profile_snapshot={},version_times={'1':'2026-10-05T00:00:00Z'})
        db.add(batch);db.flush()
        prose='合成已保存草稿中文🙂。'
        chapter=GenerationChapterVersion(owner_id=task.owner_id,generation_id=batch.id,candidate_version=1,chapter_index=1,
            content_id=uid(),title='合成草稿',text=prose,content_hash=digest(prose),completeness='complete',durability='saved')
        db.add(chapter);db.commit();gid,cid=batch.id,chapter.content_id
        before_task=(deepcopy(task.payload),deepcopy(task.result),deepcopy(task.checkpoint),task.event_seq)
    draft={**base,'kind':'draft','mode':'draft','contentRevision':gid+':1','paragraphId':cid,
        'lastCompletedAnchor':{'bookId':demo['bookId'],'generationId':gid,'contentId':cid,'candidateVersion':1,
            'start':0,'end':utf16_len(prose),'offsetUnit':'utf16','draft':True}}
    saved=put_position(client,draft,'complete-draft')
    assert saved.status_code==200 and saved.json()['readAdvance']=='not-requested',saved.text
    assert get_position(client,draft).json()['position']==saved.json()['position']
    assert put_position(client,{**draft,'expectedVersion':1,'expectedProgressVersion':0},'draft-no-read').status_code==422
    bad=deepcopy(draft);bad['lastCompletedAnchor']['candidateVersion']=2
    assert put_position(client,bad,'draft-other-version').status_code==422
    with SessionLocal() as db:
        task=db.get(Task,batch.task_id)
        assert (task.payload,task.result,task.checkpoint,task.event_seq)==before_task
        assert db.scalar(select(func.count()).select_from(TaskEvent))==0
        assert db.scalar(select(func.count()).select_from(Progress))==0
        assert db.scalar(select(func.count()).select_from(UsageRecord))==0
        chapter=db.scalar(select(GenerationChapterVersion).where(GenerationChapterVersion.content_id==cid))
        chapter.completeness='partial';db.commit()
    assert get_position(client,draft).status_code==422
    assert put_position(client,{**draft,'expectedVersion':1},'partial-no-completion').status_code==422


def test_world_private_unprojected_offsets_are_rejected_and_only_public_text_matches(client, reader):
    from app.world.models import WorldCheckpoint
    from app.world.state import private_phrases, checkpoint_hash
    from app.world.service import checkpoint_result
    body,_,_=synthetic_world(reader['user']['id'])
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,body['contentRevision'])
        definition=deepcopy(cp.definition)
        definition['lore'].append({'id':uid(),'text':'本场景尚未公开的绝密真相','knownBy':[],'entityIds':[]})
        cp.definition=definition;cp.text='公开内容，'+definition['lore'][-1]['text']+'。'
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,cp.parent_checkpoint_id)
        public=checkpoint_result(cp,include_state=False)['text']
        assert definition['lore'][-1]['text'] in private_phrases(cp.state,cp.definition)
        assert definition['lore'][-1]['text'] not in public
        rawend=utf16_len(cp.text);db.commit()
    raw=deepcopy(body);raw['lastCompletedAnchor']['end']=rawend
    assert put_position(client,raw,'raw-private-offset').status_code==422
    safe=deepcopy(body);safe['lastCompletedAnchor']['end']=utf16_len(public)
    response=put_position(client,safe,'projected-offset')
    assert response.status_code==200,response.text
    assert '绝密真相' not in response.text


def test_account_cleanup_really_cascades_owned_playback_and_preferences(client, reader, demo):
    from app import worker
    from app.models import User
    body=reader_body(client,demo);assert put_position(client,body).status_code==200
    deleted=client.post(BASE+'/auth/delete-account',json={'currentPassword':'reader-password-123','confirmation':'删除我的账号'})
    assert deleted.status_code==202,deleted.text
    worker.process(deleted.json()['taskId'])
    with SessionLocal() as db:
        assert db.get(User,reader['user']['id']) is None
        assert db.get(UserSettings,reader['user']['id']) is None
        assert db.scalar(select(func.count()).select_from(PlaybackPosition).where(PlaybackPosition.owner_id==reader['user']['id']))==0

@pytest.mark.parametrize('gap', ['\ufeff', '\u0085'])
def test_world_completion_matches_actual_ecmascript_whitespace_split(client, reader, gap):
    from app.world.models import WorldCheckpoint
    from app.world.state import checkpoint_hash
    body, _, _ = synthetic_world(reader['user']['id'])
    first, last = '首段。', '后段🙂。'
    public_text = first + '\n' + gap + '\n' + last
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, body['contentRevision'])
        cp.text = public_text
        cp.content_hash = checkpoint_hash(cp.state, cp.definition, cp.text, cp.parent_checkpoint_id)
        db.commit()
    # These are independent hand-defined ECMAScript segment boundaries, not
    # expected values generated by the server's Python split/copy helper.
    anchor = body['lastCompletedAnchor']
    if gap == '\ufeff':
        anchor['paragraph'] = 1
        anchor['start'] = utf16_len(first + '\n\ufeff\n')
    else:
        anchor['paragraph'] = 0
        anchor['start'] = 0
    anchor['end'] = utf16_len(public_text)
    body['paragraphId'] = body['contentRevision'] + ':' + str(anchor['paragraph'])
    saved = put_position(client, body, 'ecmascript-whitespace')
    assert saved.status_code == 200, saved.text
    assert saved.json()['position']['lastCompletedAnchor'] == anchor
    assert get_position(client, body).json()['position'] == saved.json()['position']


def test_world_whitespace_only_segment_is_not_a_completed_spoken_paragraph(client, reader):
    from app.world.models import WorldCheckpoint
    from app.world.state import checkpoint_hash
    body, _, _ = synthetic_world(reader['user']['id'])
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, body['contentRevision'])
        cp.text = '\ufeff'
        cp.content_hash = checkpoint_hash(cp.state, cp.definition, cp.text, cp.parent_checkpoint_id)
        db.commit()
    body['lastCompletedAnchor']['end'] = 1
    response = put_position(client, body, 'not-spoken-whitespace')
    assert response.status_code == 422 and response.json()['code'] == 'PLAYBACK_ANCHOR_INVALID', response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PlaybackPosition)) == 0
