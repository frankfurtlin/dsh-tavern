"""Official reader0013 wiring/lifecycle, real owned PostgreSQL and HTTP."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from datetime import timedelta
import json
import os
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text, event, inspect
from alembic import command
from alembic.config import Config
from app.db import SessionLocal, engine
from app.main import app
from app.models import Task, UsageRecord, Book, Revision, ContentBlock, Idempotency, Progress, User, uid, now
from app.domain import make_anchor, utf16_len, digest, canonical
from app.comparison.common import scope_key
from app.comparison.models import ComparisonExposure
from app.comparison import service as comparison
from .helpers import BASE
from .test_cloud_playback import no_provider, synthetic_world
from .test_book_deletion import legacy_publication, delete as delete_book

MAIN_COMPARE_REGISTERED_AT_IMPORT = any(route.path == BASE + '/comparison/capabilities' for route in app.routes)


def observation(name, value):
    folder = os.environ.get('PROSE_TEST_OBSERVATIONS')
    if folder:
        path = Path(folder); path.mkdir(parents=True, exist_ok=True)
        target = path / (name + '.json')
        assert not target.exists()
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('removed', ['world', 'line'])
def test_inflight_reveal_cannot_commit_range_after_world_or_line_delete(client, reader, monkeypatch, removed):
    # The pre-integration red mounts the actual router in the test process only.
    # Once main registers it officially this branch is unused; it never creates
    # a fake compare implementation or intercepts an HTTP response.
    fixture_mount = not any(route.path == BASE + '/comparison/capabilities' for route in app.routes)
    if fixture_mount:
        from app.api_comparison import router
        app.include_router(router, prefix=BASE)
    body, _, _ = synthetic_world(reader['user']['id'])
    world, line, point = body['context']['worldId'], body['lineId'], body['contentRevision']
    request = {'confirmed': True, 'kind': 'world', 'resourceId': world,
        'lineId': line, 'nodeId': point, 'expectedVersion': 0}
    arrived, release, deleting = threading.Event(), threading.Event(), threading.Event()
    original = comparison.selected_anchor
    def paused(db, owner, data):
        anchor = original(db, owner, data)
        if data.resourceId == world:
            arrived.set()
            assert release.wait(10), 'Own delete/reveal barrier was not released'
        return anchor
    monkeypatch.setattr(comparison, 'selected_anchor', paused)
    headers = dict(client.headers)
    def reveal():
        with TestClient(app, raise_server_exceptions=False) as http:
            return http.post(BASE + '/stories/world-' + world + '/compare/reveal', json=request,
                headers={**headers, 'Idempotency-Key': 'inflight-reveal-' + removed})
    def erase():
        deleting.set()
        path = '/api/world/v1/worlds/' + world
        if removed == 'line': path += '/lines/' + line
        with TestClient(app, raise_server_exceptions=False) as http:
            return http.delete(path, headers=headers)
    deleted_before_release = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(reveal)
        assert arrived.wait(10)
        deleted = pool.submit(erase)
        assert deleting.wait(10)
        try:
            deleted.result(timeout=.5)
            deleted_before_release = True
        except FutureTimeout:
            pass
        finally:
            release.set()
        saved_response, deleted_response = saved.result(10), deleted.result(10)
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(ComparisonExposure))
        tasks = db.scalar(select(func.count()).select_from(Task))
        usage = db.scalar(select(func.count()).select_from(UsageRecord))
    observation('inflight-reveal-' + removed, {'fixtureRouterMounted': fixture_mount,
        'routerRegisteredByMainAtModuleImport': MAIN_COMPARE_REGISTERED_AT_IMPORT,
        'deletedBeforeRevealBarrierReleased': deleted_before_release,
        'revealStatus': saved_response.status_code, 'deleteStatus': deleted_response.status_code,
        'remainingExposureRows': count, 'taskRows': tasks, 'usageRows': usage})
    assert saved_response.status_code == 200, saved_response.text
    assert deleted_response.status_code == 200, deleted_response.text
    assert tasks == usage == 0
    assert count == 0, 'A late compare reveal resurrected the deleted world/line view range'


def exposure(db, owner, book, line, node, anchor):
    row = ComparisonExposure(owner_id=owner, scope_key=scope_key('book', book, line, node),
        kind='book', resource_id=book, line_id=line, node_id=node, anchor=deepcopy(anchor))
    db.add(row); db.flush()
    return row


def first_anchor(db, demo):
    revision = db.get(Revision, demo['revisionId'])
    block = db.get(ContentBlock, revision.manifest[0]['blocks'][0])
    return make_anchor(demo['bookId'], demo['branchId'], revision.id, block, utf16_len(block.text))


def test_official_main_routes_ready_schema_and_lossless_empty_0013_roundtrip(client, reader, demo):
    assert sum(route.path == BASE + '/comparison/capabilities' for route in app.routes) == 1
    capability = client.get(BASE + '/comparison/capabilities')
    assert capability.status_code == 200 and capability.json()['ready'] and capability.json()['revealReady']
    assert client.get('/health/ready').status_code == 200
    with SessionLocal() as db:
        before = (db.get(Revision, demo['revisionId']).content_hash, db.get(Book, demo['bookId']).source_id)
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '0022'
    try:
        command.downgrade(Config('alembic.ini'), '0012')
        assert 'comparison_exposures' not in inspect(engine).get_table_names()
        assert client.get('/health/ready').status_code == 503
        assert not client.get(BASE + '/comparison/capabilities').json()['revealReady']
        assert client.get(BASE + '/playback/capabilities').json()['ready']
        command.upgrade(Config('alembic.ini'), 'head')
        assert client.get('/health/ready').status_code == 200
        with SessionLocal() as db:
            assert before == (db.get(Revision, demo['revisionId']).content_hash, db.get(Book, demo['bookId']).source_id)
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == '0022'
    finally:
        command.upgrade(Config('alembic.ini'), 'head')


def test_populated_0013_view_range_refuses_downgrade_before_any_data_loss(client, reader, demo):
    with SessionLocal() as db:
        row = exposure(db, reader['user']['id'], demo['bookId'], demo['branchId'], demo['revisionId'], first_anchor(db, demo))
        identity, before = row.id, deepcopy(row.anchor); db.commit()
    with pytest.raises(RuntimeError, match='Cannot downgrade saved comparison'):
        command.downgrade(Config('alembic.ini'), '0012')
    with SessionLocal() as db:
        assert db.get(ComparisonExposure, identity).anchor == before
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '0022'
    assert client.get('/health/ready').status_code == 200


def test_official_compare_on_actual_import_is_pure_and_reveal_has_independent_cas(client, reader):
    from app.worker import process
    upload = client.post(BASE + '/books/imports', files={'file': ('owned.txt',
        '第一章 灯下\n林渡收好口信🙂。\n\n第二章 渡口\n梅舟推开木门。\n'.encode('utf-8'))},
        headers={'Idempotency-Key': 'actual-compare-import'})
    assert upload.status_code == 202, upload.text
    process(upload.json()['taskId'])
    confirmed = client.post(BASE + '/books/imports/' + upload.json()['taskId'] + '/confirm',
        json={'title': '本轮合成导入'}, headers={'Idempotency-Key': 'actual-compare-confirm'})
    assert confirmed.status_code == 201, confirmed.text
    made = confirmed.json(); address = BASE + '/stories/book-' + made['bookId'] + '/compare'
    query = {'kind': 'book', 'bookId': made['bookId'], 'leftLineId': made['branchId'], 'rightLineId': made['branchId']}
    with SessionLocal() as db:
        before = (deepcopy(db.get(Revision, made['revisionId']).manifest), db.get(Revision, made['revisionId']).content_hash,
            db.scalar(select(func.count()).select_from(Task)), db.scalar(select(func.count()).select_from(UsageRecord)))
    empty = client.get(address, params=query)
    assert empty.status_code == 200 and empty.json()['left']['items'] == [], empty.text
    body = {'kind': 'book', 'resourceId': made['bookId'], 'lineId': made['branchId'],
        'nodeId': made['revisionId'], 'confirmed': True, 'expectedVersion': 0}
    saved = client.post(address + '/reveal', json=body, headers={'Idempotency-Key': 'actual-reveal'})
    assert saved.status_code == 200, saved.text
    assert client.get(address + '/reveal/requests', params={'requestId': 'actual-reveal'}).json()['result'] == saved.json()
    assert client.post(address + '/reveal', json=body, headers={'Idempotency-Key': 'actual-reveal'}).json() == saved.json()
    conflict = client.post(address + '/reveal', json=body, headers={'Idempotency-Key': 'other-device-reveal'})
    assert conflict.status_code == 409 and conflict.json()['code'] == 'COMPARE_VIEW_CONFLICT'
    read = client.get(address, params=query)
    assert read.status_code == 200 and [row['text'] for row in read.json()['left']['items']] == ['林渡收好口信🙂。', '梅舟推开木门。'], read.text
    with SessionLocal() as db:
        assert before == (deepcopy(db.get(Revision, made['revisionId']).manifest), db.get(Revision, made['revisionId']).content_hash,
            db.scalar(select(func.count()).select_from(Task)), db.scalar(select(func.count()).select_from(UsageRecord)))
        assert db.scalar(select(func.count()).select_from(Progress)) == 0


def test_source_deletion_preserves_publication_range_and_world_receipt_then_world_deletion_preserves_book_range(client, reader, demo):
    from app.world.models import WorldPublication
    body, _, _ = synthetic_world(reader['user']['id']); world = body['context']['worldId']
    with SessionLocal() as db:
        branch, revision, _, at = legacy_publication(db, reader['user']['id'], demo, world)
        revision.content_hash = digest(canonical(revision.manifest))
        retained = exposure(db, reader['user']['id'], demo['bookId'], branch.id, revision.id, at)
        source = exposure(db, reader['user']['id'], demo['bookId'], demo['branchId'], demo['revisionId'], first_anchor(db, demo))
        db.add(WorldPublication(owner_id=reader['user']['id'], world_id=world, checkpoint_id=body['contentRevision'],
            book_id=demo['bookId'], branch_id=branch.id, revision_id=revision.id, anchor=at))
        value = comparison.result(retained)
        db.add(Idempotency(owner_id=reader['user']['id'], scope='compare-reveal:world-' + world, key='kept-view',
            payload_hash=digest('own legacy request'), result=value, expires_at=now()+timedelta(days=30)))
        kept_id, source_id, branch_id, node_id = retained.id, source.id, branch.id, revision.id; db.commit()
    removed = delete_book(client, demo['bookId'])
    assert removed.status_code == 202, removed.text
    carrier = removed.json()['retainedStories'][0]['bookId']
    with SessionLocal() as db:
        kept = db.get(ComparisonExposure, kept_id)
        assert kept.resource_id == carrier and kept.anchor == {**at, 'bookId': carrier} and kept.version == 2
        assert kept.scope_key == scope_key('book', carrier, branch_id, node_id)
        assert db.get(ComparisonExposure, source_id) is None
    receipt = client.get(BASE + '/stories/world-' + world + '/compare/reveal/requests', params={'requestId': 'kept-view'})
    assert receipt.status_code == 200 and receipt.json()['result']['resourceId'] == carrier, receipt.text
    assert client.delete('/api/world/v1/worlds/' + world).status_code == 200
    address = BASE + '/stories/book-' + carrier + '/compare'
    read = client.get(address, params={'kind': 'book', 'bookId': carrier, 'leftLineId': branch_id, 'rightLineId': branch_id})
    assert read.status_code == 200 and not read.json()['left']['hasUnseen'], read.text
    with SessionLocal() as db:
        assert db.get(ComparisonExposure, kept_id) is not None


def test_batch_graph_route_has_bounded_world_queries_and_no_hidden_labels_or_prose(client, reader):
    from app.world.models import World, WorldLine, WorldCheckpoint
    from app.world.state import checkpoint_hash
    body, _, _ = synthetic_world(reader['user']['id']); world_id = body['context']['worldId']
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, body['contentRevision']); world = db.get(World, world_id)
        definition = deepcopy(cp.definition); location = next(e for e in definition['entities'] if e['id'] == cp.state['location'])
        hidden = 'PRIVATE_LOCATION_NAME'; location['name'] = hidden
        definition['lore'].append({'id': uid(), 'text': hidden, 'knownBy': []})
        cp.definition = definition; cp.summary = '不应进入图谱的正文摘要'; cp.options = ['不应进入图谱的选项']
        cp.content_hash = checkpoint_hash(cp.state, cp.definition, cp.text, cp.parent_checkpoint_id)
        old = cp.id
        for index in range(1, 40):
            new = WorldCheckpoint(id=uid(), owner_id=reader['user']['id'], world_id=world_id, line_id=body['lineId'],
                parent_checkpoint_id=old, sequence=index, state=deepcopy(cp.state), definition=deepcopy(definition),
                text='本轮合成正文', title='节点'+str(index), action='观察', kind='turn', summary='摘要不入图谱', options=[], changes=[])
            new.content_hash = checkpoint_hash(new.state, new.definition, new.text, old)
            db.add(new); old = new.id
        db.get(WorldLine, body['lineId']).head_checkpoint_id = old
        db.add(WorldLine(owner_id=reader['user']['id'], world_id=world_id, name='共享前缀子线',
            parent_line_id=body['lineId'], fork_checkpoint_id=old, head_checkpoint_id=old))
        db.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT') and 'world.' in statement:
            statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        result = client.get('/api/world/v1/worlds/' + world_id + '/graph')
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert result.status_code == 200, result.text
    data_queries = [statement for statement in statements if 'world.schema_version' not in statement]
    # The public route adds two readiness probes to the bounded four data
    # queries; record both limits rather than calling the full HTTP route four.
    assert len(statements) <= 6 and len(data_queries) <= 4
    assert len(result.json()['nodes']) == 40 and len(result.json()['lines']) == 2
    assert hidden not in result.text
    assert all(node['text'] == node['summary'] == '' and node['options'] == node['changes'] == [] for node in result.json()['nodes'])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_account_cleanup_cascades_only_own_comparison_rows(client, reader, demo):
    from app.worker import process
    owner = reader['user']['id']
    with SessionLocal() as db:
        exposure(db, owner, demo['bookId'], demo['branchId'], demo['revisionId'], first_anchor(db, demo)); db.commit()
    with TestClient(app, raise_server_exceptions=False) as other:
        from .test_world_security import _login_other
        other_owner = _login_other(other)['user']['id']
        made = other.post(BASE + '/books/demo', json={}, headers={'Idempotency-Key': 'other-demo'}).json()
        with SessionLocal() as db:
            second = exposure(db, other_owner, made['bookId'], made['branchId'], made['revisionId'], first_anchor(db, made))
            second_id = second.id; db.commit()
    deletion = client.post(BASE + '/auth/delete-account', json={'currentPassword': 'reader-password-123', 'confirmation': '删除我的账号'})
    assert deletion.status_code == 202, deletion.text
    process(deletion.json()['taskId'])
    with SessionLocal() as db:
        assert db.get(User, owner) is None and db.get(User, other_owner) is not None
        assert db.scalar(select(func.count()).select_from(ComparisonExposure).where(ComparisonExposure.owner_id == owner)) == 0
        assert db.get(ComparisonExposure, second_id) is not None


@pytest.mark.parametrize('removed', ['world', 'line'])
def test_completed_delete_rejects_late_reveal_without_recreating_range(client, reader, removed):
    body, _, _ = synthetic_world(reader['user']['id'])
    world, line, point = body['context']['worldId'], body['lineId'], body['contentRevision']
    path = '/api/world/v1/worlds/' + world
    if removed == 'line': path += '/lines/' + line
    assert client.delete(path).status_code == 200
    late = client.post(BASE + '/stories/world-' + world + '/compare/reveal',
        json={'confirmed': True, 'kind': 'world', 'resourceId': world, 'lineId': line,
            'nodeId': point, 'expectedVersion': 0},
        headers={'Idempotency-Key': 'already-deleted-' + removed})
    assert late.status_code == 404, late.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ComparisonExposure)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_disabled_world_without_world_schema_still_serves_official_book_comparison(client, reader, demo, monkeypatch):
    from app.config import settings
    from app.world.migrate import migrate
    monkeypatch.setattr(settings, 'interactive_world_enabled', False)
    with engine.begin() as connection:
        # This test owns a new empty synthetic world schema. No other test or
        # background worker runs concurrently in this internal/tmpfs project.
        assert connection.scalar(text('SELECT count(*) FROM world.instances')) == 0
        connection.execute(text('DROP SCHEMA world CASCADE'))
    try:
        assert client.get('/health/ready').status_code == 200
        assert client.get(BASE + '/comparison/capabilities').json()['revealReady']
        address = BASE + '/stories/book-' + demo['bookId'] + '/compare'
        query = {'kind': 'book', 'bookId': demo['bookId'], 'leftLineId': demo['branchId'], 'rightLineId': demo['branchId']}
        before = client.get(address, params=query)
        assert before.status_code == 200 and before.json()['left']['items'] == [], before.text
        saved = client.post(address + '/reveal', json={'confirmed': True, 'kind': 'book',
            'resourceId': demo['bookId'], 'lineId': demo['branchId'], 'nodeId': demo['revisionId'], 'expectedVersion': 0},
            headers={'Idempotency-Key': 'disabled-world-own-view'})
        assert saved.status_code == 200, saved.text
        assert client.get(address + '/reveal/requests', params={'requestId': 'disabled-world-own-view'}).json()['result'] == saved.json()
        visible = client.get(address, params=query)
        assert visible.status_code == 200 and visible.json()['left']['items'], visible.text
        with SessionLocal() as db:
            assert db.scalar(text("SELECT to_regnamespace('world')")) is None
            assert db.scalar(select(func.count()).select_from(Task)) == db.scalar(select(func.count()).select_from(UsageRecord)) == 0
            assert db.scalar(select(func.count()).select_from(Progress)) == 0
    finally:
        migrate()
