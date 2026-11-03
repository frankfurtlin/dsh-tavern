"""Current imported navigation follows real copies; history stays immutable."""
from copy import deepcopy
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import (Task, Branch, Revision, GenerationBatch,
    GenerationChapterVersion, GenerationAcceptance)
from app.domain import digest
from .helpers import BASE
from .test_prose_generations import isolated_prose
from .test_prose_imported_history import imported, original_rows, snapshot, OLD_META


def test_imported_list_and_detail_share_original_or_explicit_unknown_state(client, reader, demo):
    receipts = [imported(client, reader, demo, metadata=metadata)[0] for metadata in (True, False)]
    before = {row['generationId']: original_rows(row) for row in receipts}
    response = client.get(BASE + '/generations', params={'bookId': demo['bookId'], 'branchId': demo['branchId']})
    assert response.status_code == 200, response.text
    items = {row['id']: row for row in response.json()['items']}
    for receipt, expected in zip(receipts, ('failed', 'historical-unknown')):
        detail = snapshot(client, receipt)
        assert detail['state'] == expected
        assert items[receipt['generationId']]['state'] == detail['state']
        assert original_rows(receipt) == before[receipt['generationId']]


def test_imported_two_prefix_copies_show_actual_head_keep_old_version_and_task(client, reader, demo, monkeypatch):
    receipt, request = imported(client, reader, demo)
    old_metadata = {**OLD_META, 'targetBranchId': demo['branchId'],
        'lastAcceptedRevisionId': request['expectedHeadRevisionId']}
    with SessionLocal() as db:
        task = db.get(Task, receipt['taskId'])
        task.checkpoint = {**task.checkpoint, 'proseVersionStates': {'1': deepcopy(old_metadata)}}
        second = db.scalar(select(GenerationChapterVersion).where(
            GenerationChapterVersion.generation_id == receipt['generationId'],
            GenerationChapterVersion.candidate_version == 2,
            GenerationChapterVersion.chapter_index == 2))
        second.text = '这是已保存的合成第二章，角色核对线索后回到渡口。\n\n' * 40
        second.content_hash = digest(second.text); second.completeness = 'complete'
        assert second.durability == 'saved'
        source_head = db.get(Branch, demo['branchId']).head_revision_id
        revisions = db.scalar(select(func.count()).select_from(Revision))
        db.commit()
    before = original_rows(receipt)
    archived = snapshot(client, receipt, candidateVersion=1)
    assert {key: archived[key] for key in old_metadata} == old_metadata
    def forbidden(*args, **kwargs):
        raise AssertionError('A saved historical prefix copy must not call a provider or stream journal')
    monkeypatch.setattr('app.worker.call_provider', forbidden)
    monkeypatch.setattr('app.prose.stream.overlay', forbidden)
    expected_head = request['expectedHeadRevisionId']
    target_id = None
    for prefix in (1, 2):
        body = {'candidateVersion': 2, 'prefixCount': prefix, 'expectedAcceptedPrefix': prefix - 1,
            'expectedHeadRevisionId': expected_head, 'target': 'child' if prefix == 1 else 'append',
            'sourcePolicy': 'requireUnchanged'}
        headers = {'Idempotency-Key': 'historical-copy-' + str(prefix)}
        response = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept', json=body, headers=headers)
        assert response.status_code == 200, response.text
        repeated = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept', json=body, headers=headers)
        assert repeated.status_code == 200 and repeated.json() == response.json()
        accepted = response.json(); current = snapshot(client, receipt)
        with SessionLocal() as db:
            batch = db.get(GenerationBatch, receipt['generationId'])
            target = db.get(Branch, accepted['branchId'])
            assert current['acceptedPrefix'] == batch.accepted_prefix == prefix
            assert current['targetBranchId'] == batch.target_branch_id == target.id == accepted['branchId']
            assert current['lastAcceptedRevisionId'] == batch.last_accepted_revision_id == target.head_revision_id == accepted['revisionId']
            assert db.get(Branch, demo['branchId']).head_revision_id == source_head
            assert db.scalar(select(func.count()).select_from(GenerationAcceptance)) == prefix
            assert db.scalar(select(func.count()).select_from(Revision)) == revisions + prefix
        if target_id is not None:
            assert accepted['branchId'] == target_id
        target_id = accepted['branchId']; expected_head = accepted['revisionId']
        assert target_id != demo['branchId']
        assert snapshot(client, receipt, candidateVersion=1) == archived
        assert original_rows(receipt) == before
        assert before['profiles'] == before['consent'] == before['usage'] == 0
    assert all(not chapter['canAccept'] for chapter in current['chapters'])
