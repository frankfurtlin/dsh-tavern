"""Actual registered routes, additive DDL and deletion/idle lifecycle hooks."""
from copy import deepcopy
from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, func, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import engine, SessionLocal
from app.main import app
from app.models import User, Book, Branch, SourceDocument, Task, UsageRecord, CleanupReceipt, uid, now
from app.assets.models import ImageAsset, AssetVariant, AssetPlacement
from app.assets.schemas import PlacementInput
from app.assets.service import create_asset, place_asset
from app.portable.models import PortableOperation
from app.portable.errors import ArchiveError
from app.portable import service as portable
from app.portable.upload_limit import MAX_REQUEST_BYTES
from app.providers import MockAdapter, ChatCompletionsAdapter
from app.storage import private_path
from app import worker
from app.world.models import World, WorldLine, WorldCheckpoint
from app.world.state import initial_state, checkpoint_hash
from .helpers import BASE
from .test_book_deletion import legacy_publication


@pytest.fixture(autouse=True)
def own_mock_only(reset_db, monkeypatch, tmp_path):
    assert engine.url.database == 'reader_test' and settings.ai_mode == 'mock'
    monkeypatch.setattr(settings, 'storage_path', tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Registration/deletion/maintenance cannot invoke any provider')
    for adapter in (MockAdapter, ChatCompletionsAdapter):
        monkeypatch.setattr(adapter, 'generate', forbidden)
        monkeypatch.setattr(adapter, 'generate_stream', forbidden)


def book_scope(demo):
    return {'kind': 'book', 'bookId': demo['bookId'], 'branchId': demo['branchId'], 'revisionId': demo['revisionId']}


def image(owner, scope):
    output = BytesIO(); Image.new('RGB', (120, 180), '#638472').save(output, format='PNG')
    with SessionLocal() as db:
        result = create_asset(db, owner, output.getvalue(), 'image/png', 'cover',
            {'x': 0, 'y': 0, 'width': 120, 'height': 180}, scope, '自编合成测试图，CC0')
        placement = place_asset(db, owner, PlacementInput(assetId=result['assetId'], scope=scope, purpose='cover'))
        db.commit()
        return result['assetId'], placement['id']


def counts():
    with SessionLocal() as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (ImageAsset, AssetVariant, AssetPlacement, PortableOperation, Task, UsageRecord)}


def stage(owner, kind='export', expired=False):
    prefix = 'reader-bk-import-' if kind == 'restore' else 'reader-bk-download-'
    name = prefix + uid(); root = portable.directory(owner) / name; root.mkdir()
    (root / 'synthetic-private.txt').write_bytes(b'owned synthetic stage')
    with SessionLocal() as db:
        row = PortableOperation(owner_id=owner, kind=kind, request_key=uid(), payload_hash='a' * 64,
            status='ready', stage_name=name, expires_at=now() + timedelta(minutes=-31 if expired else 30),
            counts={}, result={})
        db.add(row); db.commit(); identifier = row.id
    return identifier, root


def source_record(demo):
    with SessionLocal() as db:
        book = db.get(Book, demo['bookId']); source = db.get(SourceDocument, book.source_id)
        return {'book': book.id, 'source': source.id, 'branch': demo['branchId'],
            'head': db.get(Branch, demo['branchId']).head_revision_id,
            'sourceMap': deepcopy(source.source_map),
            'bytes': private_path(book.owner_id, source.normalized_ref).read_bytes()}


def test_registered_routes_ready_and_backup_early_limit_without_any_operation(client, reader, demo):
    assert client.get('/health/ready').status_code == 200
    for path, protocol in (('/assets/capabilities', 'reader-assets-v1'), ('/backups/capabilities', 'reader-portable-v1')):
        response = client.get(BASE + path)
        assert response.status_code == 200, response.text
        assert response.json()['ready'] and response.json()['protocol'] == protocol
    before = counts(); source = source_record(demo)
    response = client.post(BASE + '/backups/prepare', content=b'x',
        headers={'Content-Length': str(MAX_REQUEST_BYTES + 1)})
    assert response.status_code == 413 and response.json()['code'] == 'BACKUP_SIZE_LIMIT', response.text
    assert response.headers['X-Request-ID'] and response.headers['Cache-Control'] == 'no-store'
    assert counts() == before and source_record(demo) == source
    assert not (settings.storage_path / '.portable').exists()


def test_0010_0011_empty_roundtrip_preserves_source_and_actual_capabilities(client, reader, demo):
    before = source_record(demo); config = Config('alembic.ini')
    expected = {'image_assets', 'image_asset_variants', 'image_asset_placements', 'portable_operations'}
    try:
        command.downgrade(config, '0009')
        assert not expected & set(inspect(engine).get_table_names())
        for path in ('/assets/capabilities', '/backups/capabilities'):
            response = client.get(BASE + path); assert response.status_code == 200, response.text
            assert response.json()['ready'] is False
        assert source_record(demo) == before
        command.upgrade(config, 'head')
        assert expected <= set(inspect(engine).get_table_names())
        with SessionLocal() as db:
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == '0022'
        assert source_record(demo) == before
        assert client.get('/health/ready').status_code == 200
    finally:
        command.upgrade(config, 'head')


@pytest.mark.parametrize('kind', ['image', 'operation'])
def test_populated_additive_migrations_refuse_lossy_downgrade_before_ddl(client, reader, demo, kind):
    owner = reader['user']['id']
    if kind == 'image':
        asset_id, _ = image(owner, book_scope(demo))
    else:
        operation_id, root = stage(owner, kind='reading_export')
    before = counts(); source = source_record(demo)
    with pytest.raises(RuntimeError, match='Cannot downgrade'):
        command.downgrade(Config('alembic.ini'), '0009')
    assert counts() == before and source_record(demo) == source
    with SessionLocal() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '0022'
        if kind == 'image':
            assert db.get(ImageAsset, asset_id) is not None
            assert db.scalar(select(func.count()).select_from(AssetVariant).where(AssetVariant.asset_id == asset_id)) > 0
        else:
            assert db.get(PortableOperation, operation_id) is not None and root.is_dir()


def test_book_delete_cleans_owned_image_bytes_in_same_committed_transaction(client, reader, demo):
    asset_id, _ = image(reader['user']['id'], book_scope(demo))
    with SessionLocal() as db: version = db.get(Book, demo['bookId']).version
    response = client.request('DELETE', BASE + '/books/' + demo['bookId'], json={'expectedVersion': version},
        headers={'Idempotency-Key': 'registered-book-delete'})
    assert response.status_code == 202 and response.json()['deleted'], response.text
    with SessionLocal() as db:
        assert db.get(Book, demo['bookId']) is None
        assert db.get(ImageAsset, asset_id) is None
        assert db.scalar(select(func.count()).select_from(AssetVariant).where(AssetVariant.asset_id == asset_id)) == 0
        assert db.scalar(select(func.count()).select_from(AssetPlacement).where(AssetPlacement.asset_id == asset_id)) == 0


def test_source_delete_detaches_legacy_publication_image_without_changing_bytes(client, reader, demo):
    owner = reader['user']['id']
    with SessionLocal() as db:
        branch, revision, block, anchor = legacy_publication(db, owner, demo, uid())
        target = {'kind': 'book', 'bookId': demo['bookId'], 'branchId': branch.id, 'revisionId': revision.id}
        db.commit()
    asset_id, placement_id = image(owner, target)
    with SessionLocal() as db:
        variants = [(r.name, r.sha256, r.data) for r in db.scalars(select(AssetVariant).where(AssetVariant.asset_id == asset_id).order_by(AssetVariant.name))]
        version = db.get(Book, demo['bookId']).version
    response = client.request('DELETE', BASE + '/books/' + demo['bookId'], json={'expectedVersion': version},
        headers={'Idempotency-Key': 'registered-source-image-detach'})
    assert response.status_code == 202, response.text
    retained = response.json()['retainedStories']; assert len(retained) == 1
    rebound = {**target, 'bookId': retained[0]['bookId']}
    with SessionLocal() as db:
        assert db.get(Book, demo['bookId']) is None
        assert db.get(Branch, target['branchId']).book_id == rebound['bookId']
        assert db.get(ImageAsset, asset_id).origin_scope == rebound
        assert db.get(AssetPlacement, placement_id).scope == rebound
        assert [(r.name, r.sha256, r.data) for r in db.scalars(select(AssetVariant).where(AssetVariant.asset_id == asset_id).order_by(AssetVariant.name))] == variants


def two_lines(client):
    made = client.post('/api/world/v1/worlds', json={'templateId': 'original-mystery-v1'},
        headers={'Idempotency-Key': 'registered-image-world'})
    assert made.status_code == 202, made.text
    with SessionLocal() as db:
        world = db.get(World, made.json()['worldId'])
        actor = next(e['id'] for e in world.definition['entities'] if e['type'] == 'character')
        definition, state = initial_state(world.definition, actor)
        lines = []
        for index in (1, 2):
            line = WorldLine(owner_id=world.owner_id, world_id=world.id, name='合成图片线' + str(index), head_checkpoint_id=uid())
            db.add(line); db.flush()
            cp = WorldCheckpoint(id=line.head_checkpoint_id, owner_id=world.owner_id, world_id=world.id,
                line_id=line.id, sequence=1, title='公开现场', text='街灯照亮门口。', action='', state=deepcopy(state),
                definition=deepcopy(definition), options=[], changes=[], summary='', memory={}, kind='opening',
                content_hash=checkpoint_hash(state, definition, '街灯照亮门口。', None))
            db.add(cp)
            lines.append({'kind': 'world', 'worldId': world.id, 'lineId': line.id, 'checkpointId': cp.id})
        world.active_line_id = lines[0]['lineId']; db.commit()
    return lines


def test_line_delete_rebinds_surviving_reference_world_delete_collects_bytes(client, reader, demo):
    first, second = two_lines(client); owner = reader['user']['id']; source = source_record(demo)
    asset_id, _ = image(owner, first)
    with SessionLocal() as db:
        place_asset(db, owner, PlacementInput(assetId=asset_id, scope=second, purpose='cover')); db.commit()
        hashes = [(row.name, row.sha256, row.data) for row in db.scalars(select(AssetVariant).where(AssetVariant.asset_id == asset_id).order_by(AssetVariant.name))]
    response = client.delete('/api/world/v1/worlds/' + first['worldId'] + '/lines/' + first['lineId'])
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(ImageAsset, asset_id).origin_scope == second
        assert list(db.scalars(select(AssetPlacement.scope).where(AssetPlacement.asset_id == asset_id))) == [second]
        assert [(r.name, r.sha256, r.data) for r in db.scalars(select(AssetVariant).where(AssetVariant.asset_id == asset_id).order_by(AssetVariant.name))] == hashes
    response = client.delete('/api/world/v1/worlds/' + first['worldId'])
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(ImageAsset, asset_id) is None
        assert db.scalar(select(func.count()).select_from(AssetPlacement).where(AssetPlacement.asset_id == asset_id)) == 0
        assert db.scalar(select(func.count()).select_from(AssetVariant).where(AssetVariant.asset_id == asset_id)) == 0
    assert source_record(demo) == source


def test_world_delete_commit_failure_rolls_back_image_scope_and_bytes(client, reader, demo, monkeypatch):
    first, _ = two_lines(client); asset_id, placement_id = image(reader['user']['id'], first)
    with SessionLocal() as db:
        variants = [(r.name, r.sha256, r.data) for r in db.scalars(select(AssetVariant).where(AssetVariant.asset_id == asset_id).order_by(AssetVariant.name))]
    original_commit = Session.commit; failures = []
    def fail_world_delete(db):
        if any(isinstance(row, World) and row.id == first['worldId'] for row in db.deleted):
            failures.append(first['worldId'])
            raise OperationalError('synthetic DELETE commit', {}, RuntimeError('owned mock disconnect'))
        return original_commit(db)
    monkeypatch.setattr(Session, 'commit', fail_world_delete)
    response = client.delete('/api/world/v1/worlds/' + first['worldId'])
    assert response.status_code == 503 and failures == [first['worldId']], response.text
    with SessionLocal() as db:
        assert db.get(World, first['worldId']) is not None
        assert db.get(ImageAsset, asset_id).origin_scope == first
        assert db.get(AssetPlacement, placement_id).scope == first
        assert [(r.name, r.sha256, r.data) for r in db.scalars(select(AssetVariant).where(AssetVariant.asset_id == asset_id).order_by(AssetVariant.name))] == variants


def test_account_purge_after_marker_failure_recovers_without_touching_other_owner(client, reader, demo, monkeypatch):
    owner = reader['user']['id']; asset_id, _ = image(owner, book_scope(demo)); _, root = stage(owner)
    with TestClient(app, raise_server_exceptions=False) as other:
        assert other.post(BASE + '/auth/register', json={'username': 'media_other', 'password': 'synthetic-password-123'}).status_code == 201
        credentials = other.post(BASE + '/auth/login', json={'username': 'media_other', 'password': 'synthetic-password-123', 'clientType': 'web', 'deviceName': 'own-synthetic'}).json()
        other.headers['Authorization'] = 'Bearer ' + credentials['accessToken']
        other_demo = other.post(BASE + '/books/demo', json={}, headers={'Idempotency-Key': 'other-demo'}).json()
    other_owner = credentials['user']['id']; other_asset, _ = image(other_owner, book_scope(other_demo)); _, other_root = stage(other_owner)
    other_source = source_record(other_demo); other_bytes = (other_root / 'synthetic-private.txt').read_bytes()
    original = portable.purge_owner_files; calls = []
    def failing_once(target):
        assert target == owner and (settings.storage_path / '.deleted' / target).is_file()
        calls.append(target)
        if len(calls) == 1: raise OSError('synthetic owned stage removal unavailable')
        return original(target)
    monkeypatch.setattr(portable, 'purge_owner_files', failing_once)
    response = client.post(BASE + '/auth/delete-account', json={'currentPassword': 'reader-password-123', 'confirmation': '删除我的账号'})
    assert response.status_code == 202, response.text
    receipt = response.json(); worker.process(receipt['taskId'])
    with SessionLocal() as db:
        assert db.get(User, owner).status == 'deleting'
        task = db.get(Task, receipt['taskId']); assert task.state == 'failed' and task.persistence_state == 'recoverable'
        assert db.get(CleanupReceipt, receipt['receiptId']).state == 'retrying'
        assert db.get(ImageAsset, asset_id) is not None
    assert root.is_dir() and calls == [owner]
    claimed = worker.claim('synthetic-registration-retry')
    assert claimed == receipt['taskId']; worker.process(claimed)
    with SessionLocal() as db:
        assert db.get(User, owner) is None and db.get(ImageAsset, asset_id) is None
        assert db.scalar(select(func.count()).select_from(PortableOperation).where(PortableOperation.owner_id == owner)) == 0
        assert db.get(ImageAsset, other_asset) is not None
    assert calls == [owner, owner] and not root.exists()
    assert source_record(other_demo) == other_source and (other_root / 'synthetic-private.txt').read_bytes() == other_bytes
    assert client.get(BASE + '/auth/deletion-receipts/' + receipt['receiptId']).json()['status'] == 'completed'
    with pytest.raises(ArchiveError) as stopped: portable.directory(owner)
    assert getattr(stopped.value, 'code', None) == 'BACKUP_OWNER_NOT_ACTIVE'


def test_actual_idle_worker_tick_has_bounded_stage_cleanup_without_models(client, reader, demo, monkeypatch):
    stages = [stage(reader['user']['id'], kind=('export', 'restore', 'reading_export')[index % 3], expired=True) for index in range(21)]
    owner = reader['user']['id']; orphan_id, orphan_placement = image(owner, book_scope(demo))
    retained_id, _ = image(owner, book_scope(demo))
    with SessionLocal() as db:
        # The stored upload can outlive an abandoned placement request.
        db.delete(db.get(AssetPlacement, orphan_placement))
        for identifier in (orphan_id, retained_id): db.get(ImageAsset, identifier).created_at = now() - timedelta(days=2)
        db.commit()
    class StopLoop(Exception): pass
    monkeypatch.setattr(worker, 'claim', lambda _: None)
    monkeypatch.setattr(worker.time, 'monotonic', lambda: 100)
    monkeypatch.setattr(worker.time, 'sleep', lambda _: (_ for _ in ()).throw(StopLoop()))
    with pytest.raises(StopLoop): worker.run()
    with SessionLocal() as db:
        rows = list(db.scalars(select(PortableOperation)))
        assert sum(row.stage_name is None and row.status == 'expired' for row in rows) == 20
        assert sum(root.exists() for _, root in stages) == 1
        assert db.get(ImageAsset, orphan_id) is None and db.get(ImageAsset, retained_id) is not None
        assert db.scalar(select(func.count()).select_from(Task)) == db.scalar(select(func.count()).select_from(UsageRecord)) == 0
    assert portable.maintenance() == 1 and not any(root.exists() for _, root in stages)
