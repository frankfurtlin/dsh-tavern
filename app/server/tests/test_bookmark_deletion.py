"""Versioned owner-only bookmark deletion; known/unknown receipts never recreate."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.db import engine
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.models import Book, Bookmark, Branch, Progress, Idempotency, UsageRecord, Task, uid
from .helpers import BASE, read_chapter, observe
from .test_reader_plus import other_owner
from .test_world_security import isolated_storage_and_mock_only


def created(client, demo, key='note-create', note='中文🙂 precise private note'):
    anchor = read_chapter(client, demo)['blocks'][0]['endAnchor']
    path = BASE + '/books/' + demo['bookId'] + '/bookmarks'
    body = {'anchor': anchor, 'note': note}
    response = client.post(path, json=body, headers={'Idempotency-Key': key})
    assert response.status_code == 201, response.text
    return path + '/' + response.json()['id'], response.json(), body


def remove(client, path, version, key='note-delete'):
    return client.request('DELETE', path, json={'expectedVersion': version}, headers={'Idempotency-Key': key})


def test_delete_requires_current_version_and_preserves_other_device_edit(client,reader,demo):
    path, note, _ = created(client, demo)
    changed = client.patch(path, json={'expectedVersion': 1, 'note': '另一设备的新内容🙂'})
    assert changed.status_code == 200 and changed.json()['version'] == 2
    rejected = remove(client, path, 1)
    assert rejected.status_code == 409 and rejected.json()['code'] == 'NOTE_CONFLICT', rejected.text
    assert client.get(path).json()['note'] == '另一设备的新内容🙂'
    with SessionLocal() as db:
        assert db.get(Bookmark, note['id']).version == 2
        assert not db.scalar(select(func.count()).select_from(Idempotency).where(
            Idempotency.scope == 'bookmark-delete:' + demo['bookId'] + ':' + note['id']))


def test_successful_delete_receipt_is_replayable_and_create_key_cannot_return_private_note(client,reader,demo):
    path, note, body = created(client, demo)
    observe(client, reader, body['anchor'])
    with SessionLocal() as db:
        source = db.get(Book, demo['bookId']).source_hash
        head = db.get(Branch, demo['branchId']).head_revision_id
        progress = deepcopy(db.scalar(select(Progress).where(Progress.branch_id == demo['branchId'])).anchor)
        budget = (db.scalar(select(func.count()).select_from(Task)), db.scalar(select(func.count()).select_from(UsageRecord)))
    deleted = remove(client, path, 1)
    assert deleted.status_code == 200, deleted.text
    receipt = deleted.json()
    assert set(receipt) == {'deleted', 'bookmarkId', 'bookId', 'version', 'receiptId', 'deletedAt'}
    assert receipt['deleted'] and receipt['bookmarkId'] == note['id'] and receipt['bookId'] == demo['bookId']
    assert receipt['version'] == 2 and len(receipt['receiptId']) == 32
    assert remove(client, path, 1).json() == receipt
    lookup = client.get(path + '/deletion', params={'requestId': 'note-delete'})
    assert lookup.status_code == 200 and lookup.json() == {'found': True, 'receipt': receipt}, lookup.text
    assert client.get(path).status_code == 404
    assert client.get(BASE + '/notebook').json()['items'] == []
    assert client.get(BASE + '/books/' + demo['bookId'] + '/bookmarks', params={
        'branchId': demo['branchId'], 'revisionId': demo['revisionId']}).json()['items'] == []
    replay = client.post(path.rsplit('/', 1)[0], json=body, headers={'Idempotency-Key': 'note-create'})
    assert replay.status_code == 404 and replay.json()['code'] == 'BOOKMARK_DELETED', replay.text
    assert replay.json()['details'] == {'bookId': demo['bookId'], 'bookmarkId': note['id']}
    assert body['note'] not in replay.text
    with SessionLocal() as db:
        row = db.get(Bookmark, note['id'])
        assert row.status == 'deleted' and row.anchor == {} and row.note == '' and row.version == 2
        assert db.get(Book, demo['bookId']).source_hash == source
        assert db.get(Branch, demo['branchId']).head_revision_id == head
        assert db.scalar(select(Progress).where(Progress.branch_id == demo['branchId'])).anchor == progress
        assert (db.scalar(select(func.count()).select_from(Task)), db.scalar(select(func.count()).select_from(UsageRecord))) == budget
        old_create = db.scalar(select(Idempotency).where(Idempotency.key == 'note-create'))
        assert old_create.result == receipt and body['note'] not in str(old_create.result)


def test_receipt_lookup_is_pure_owner_and_book_scoped_and_unknown_does_not_delete(client,reader,demo):
    path, note, _ = created(client, demo)
    unknown = client.get(path + '/deletion', params={'requestId': 'never-sent'})
    assert unknown.status_code == 200 and unknown.json() == {'found': False}, unknown.text
    assert client.get(path).status_code == 200
    from app.worker import process
    uploaded = client.post(BASE + '/books/imports', files={'file': ('second.txt',
        '第一章\n另一部完全不同的合成小说。\n'.encode(), 'text/plain')}, headers={'Idempotency-Key': 'other-book-upload'})
    assert uploaded.status_code == 202, uploaded.text
    process(uploaded.json()['taskId'])
    confirmed = client.post(BASE + '/books/imports/' + uploaded.json()['taskId'] + '/confirm',
        json={'title': '独立第二本', 'parserVersion': 'import-v1'}, headers={'Idempotency-Key': 'other-book-confirm'})
    assert confirmed.status_code == 201, confirmed.text
    another = confirmed.json()
    assert another['bookId'] != demo['bookId']
    wrong_book = BASE + '/books/' + another['bookId'] + '/bookmarks/' + note['id']
    assert client.get(wrong_book + '/deletion', params={'requestId': 'note-delete'}).status_code == 404
    assert remove(client, path, 1).status_code == 200
    other_owner(client)
    for route in (path, path + '/deletion?requestId=note-delete'):
        response = client.get(route)
        assert response.status_code == 404 and response.json()['code'] == 'RESOURCE_NOT_FOUND', response.text
        assert note['id'] not in response.text
    denied = remove(client, path, 1)
    assert denied.status_code == 404 and denied.json()['details'] == {}


def test_old_successful_creation_key_never_replays_deleted_note_body(client,reader,demo):
    path, note, body = created(client, demo)
    assert remove(client, path, 1).status_code == 200
    replay = client.post(path.rsplit('/', 1)[0], json=body, headers={'Idempotency-Key': 'note-create'})
    assert replay.status_code == 404 and replay.json()['code'] == 'BOOKMARK_DELETED', replay.text
    assert body['note'] not in replay.text
    with SessionLocal() as db:
        assert db.get(Bookmark, note['id']).status == 'deleted'
        assert db.scalar(select(func.count()).select_from(Bookmark).where(Bookmark.status != 'deleted')) == 0


@pytest.mark.parametrize('version', [True, 0, '1', 1.5])
def test_delete_version_is_strict_integer_and_never_mutates_on_invalid_request(client,reader,demo,version):
    path, note, body = created(client, demo)
    refused = remove(client, path, version)
    assert refused.status_code == 422, refused.text
    assert client.get(path).json()['note'] == body['note']
    assert client.get(path + '/deletion', params={'requestId': 'note-delete'}).json() == {'found': False}
    with SessionLocal() as db:
        assert db.get(Bookmark, note['id']).version == 1


def test_delete_requires_key_and_version_but_ownership404_stays_authoritative(client,reader,demo):
    path, _, body = created(client, demo)
    assert client.delete(path).status_code == 422
    assert client.request('DELETE', path, json={'expectedVersion': 1}).status_code == 422
    assert client.get(path).json()['note'] == body['note']
    other_owner(client)
    assert client.delete(path).status_code == 404


def test_receipt_payload_conflict_deleted_patch_and_other_note_are_isolated(client,reader,demo):
    path, note, _ = created(client, demo)
    other_path, other, other_body = created(client, demo, key='second-note-create', note='另一条自己的笔记')
    receipt = remove(client, path, 1).json()
    conflict = remove(client, path, 2)
    assert conflict.status_code == 409 and conflict.json()['code'] == 'IDEMPOTENCY_CONFLICT'
    gone = remove(client, path, 2, key='different-delete-intent')
    assert gone.status_code == 404 and gone.json()['code'] == 'BOOKMARK_DELETED'
    edited = client.patch(path, json={'expectedVersion': 2, 'note': '迟到的私人文字'})
    assert edited.status_code == 404 and edited.json()['code'] == 'BOOKMARK_DELETED'
    assert client.get(path + '/deletion', params={'requestId': 'note-delete'}).json()['receipt'] == receipt
    replay_other = client.post(other_path.rsplit('/', 1)[0], json=other_body,
        headers={'Idempotency-Key': 'second-note-create'})
    assert replay_other.status_code == 201 and replay_other.json()['id'] == other['id']
    assert client.get(other_path).json()['note'] == other_body['note']
    with SessionLocal() as db:
        assert db.get(Bookmark, note['id']).note == ''


def test_two_real_http_connections_serialize_edit_against_delete_without_lost_update(client,reader,demo):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from app.main import app
    path, note, _ = created(client, demo)
    barrier = Barrier(2)
    def mutate(deleting):
        with TestClient(app, raise_server_exceptions=False) as own:
            own.headers['Authorization'] = 'Bearer ' + reader['accessToken']
            barrier.wait(timeout=10)
            return remove(own, path, 1) if deleting else own.patch(path,
                json={'expectedVersion': 1, 'note': '已确认的另一设备修改'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted, changed = [f.result(timeout=20) for f in (pool.submit(mutate, True), pool.submit(mutate, False))]
    if changed.status_code == 200:
        assert deleted.status_code == 409 and deleted.json()['code'] == 'NOTE_CONFLICT'
        assert client.get(path).json()['note'] == '已确认的另一设备修改'
    else:
        assert deleted.status_code == 200 and changed.status_code == 404 and changed.json()['code'] == 'BOOKMARK_DELETED'
        with SessionLocal() as db:
            assert db.get(Bookmark, note['id']).note == ''
    with SessionLocal() as db:
        assert db.get(Bookmark, note['id']).version == 2
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_source_delete_and_note_delete_lock_order_has_no_private_receipt_resurrection(client,reader,demo):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from app.main import app
    path, note, body = created(client, demo)
    barrier = Barrier(2)
    def mutate(source):
        with TestClient(app, raise_server_exceptions=False) as own:
            own.headers['Authorization'] = 'Bearer ' + reader['accessToken']
            barrier.wait(timeout=10)
            return own.request('DELETE', BASE + '/books/' + demo['bookId'], json={'expectedVersion': 1},
                headers={'Idempotency-Key': 'delete-whole-source'}) if source else remove(own, path, 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        source, deleted = [f.result(timeout=20) for f in (pool.submit(mutate, True), pool.submit(mutate, False))]
    assert source.status_code == 202, source.text
    assert deleted.status_code in (200, 404), deleted.text
    if deleted.status_code == 404: assert deleted.json()['code'] == 'BOOK_DELETED'
    with SessionLocal() as db:
        assert db.get(Book, demo['bookId']) is None and db.get(Bookmark, note['id']) is None
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        assert all(body['note'] not in str(r.result) for r in db.scalars(select(Idempotency)))


def test_0008_normal_roundtrip_preserves_legacy_note_and_tombstones_refuse_lossy_downgrade(client,reader,demo):
    path, note, body = created(client, demo)
    with SessionLocal() as db:
        old = deepcopy((db.get(Bookmark, note['id']).anchor, db.get(Bookmark, note['id']).note,
            db.get(Bookmark, note['id']).version, db.get(Bookmark, note['id']).created_at))
    cfg = Config('alembic.ini'); command.downgrade(cfg, '0007')
    try:
        assert client.get('/health/ready').status_code == 503
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_name='bookmarks' "
                "AND column_name IN ('deletion_id','deleted_at')")) == 0
        command.upgrade(cfg, 'head')
        assert client.get('/health/ready').status_code == 200
        with SessionLocal() as db:
            row = db.get(Bookmark, note['id'])
            assert (row.anchor, row.note, row.version, row.created_at) == old
            assert row.deleted_at is None and row.deletion_id is None
        assert client.post(path.rsplit('/', 1)[0], json=body, headers={'Idempotency-Key': 'note-create'}).status_code == 201
        receipt = remove(client, path, 1).json()
        with engine.connect() as connection:
            previous_version = connection.scalar(text('SELECT version_num FROM alembic_version'))
        with pytest.raises(RuntimeError, match='bookmark deletion tombstones'):
            command.downgrade(cfg, '0007')
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == previous_version
        assert client.get(path + '/deletion', params={'requestId': 'note-delete'}).json()['receipt'] == receipt
        assert client.get(path).status_code == 404
    finally:
        command.upgrade(cfg, 'head')


def test_delete_commit_failure_rolls_back_note_and_both_receipts_before_explicit_retry(client,reader,demo,monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import OperationalError
    path, note, body = created(client, demo)
    original = Session.commit
    def reject(db):
        if db.scalar(select(Idempotency).where(Idempotency.scope == 'bookmark-delete:' + demo['bookId'] + ':' + note['id'])):
            raise OperationalError('controlled note-delete commit failure', {}, RuntimeError('synthetic fixture'))
        return original(db)
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', reject)
        failed = remove(client, path, 1)
    assert failed.status_code == 503, failed.text
    assert client.get(path).json()['note'] == body['note']
    assert client.get(path + '/deletion', params={'requestId': 'note-delete'}).json() == {'found': False}
    assert client.post(path.rsplit('/', 1)[0], json=body, headers={'Idempotency-Key': 'note-create'}).json()['note'] == body['note']
    with SessionLocal() as db:
        row = db.get(Bookmark, note['id'])
        assert row.status == 'valid' and row.version == 1 and row.deletion_id is None
    assert remove(client, path, 1).json()['version'] == 2


def test_account_purge_removes_deleted_bookmark_metadata_and_delete_receipts(client,reader,demo):
    from app.worker import process
    path, _, _ = created(client, demo)
    assert remove(client, path, 1).status_code == 200
    owner = reader['user']['id']
    accepted = client.post(BASE + '/auth/delete-account', json={
        'currentPassword': 'reader-password-123', 'confirmation': '删除我的账号'})
    assert accepted.status_code == 202, accepted.text
    process(accepted.json()['taskId'])
    with SessionLocal() as db:
        for model in (Bookmark, Idempotency):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id == owner)) == 0
