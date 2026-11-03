"""Real API protocol separation; staged until the frozen first run completes."""
from copy import deepcopy
from pathlib import Path
import json
import os
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Task, TaskEvent, UsageRecord, Branch
from .helpers import BASE
from .test_prose_generations import start, isolated_prose


def task_state(task_id, branch_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        return {'state': task.state, 'cancelRequested': task.cancel_requested,
            'checkpoint': deepcopy(task.checkpoint), 'result': deepcopy(task.result),
            'eventSeq': task.event_seq,
            'taskEvents': db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id)),
            'usage': db.scalar(select(func.count()).select_from(UsageRecord)),
            'sourceHead': db.get(Branch, branch_id).head_revision_id}


def test_legacy_story_run_cannot_read_or_cancel_prose_task(client, reader, demo, profile):
    receipt, payload = start(client, reader, demo, profile)
    before = task_state(receipt['taskId'], demo['branchId'])
    read = client.get(BASE + '/story-runs/' + receipt['taskId'])
    cancel = client.post(BASE + '/story-runs/' + receipt['taskId'] + '/cancel')
    after = task_state(receipt['taskId'], demo['branchId'])
    prose = client.get(BASE + '/generations/' + receipt['generationId'])

    legacy_body = {'operation': 'counterfactual', 'profileId': profile['id'],
        'anchor': payload['originAnchor'], 'expectedHeadRevisionId': payload['expectedHeadRevisionId'],
        'instruction': '核对证言。', 'chapterCount': 1}
    legacy = client.post(BASE + '/story-runs', json=legacy_body,
        headers={'Idempotency-Key': 'legacy-compatible'})
    assert legacy.status_code == 202, legacy.text
    legacy_id = legacy.json()['id']
    legacy_read = client.get(BASE + '/story-runs/' + legacy_id)
    legacy_cancel = client.post(BASE + '/story-runs/' + legacy_id + '/cancel')
    evidence = {'proseTaskId': receipt['taskId'], 'generationId': receipt['generationId'],
        'legacyReadStatus': read.status_code, 'legacyCancelStatus': cancel.status_code,
        'before': before, 'after': after, 'newProtocolReadStatus': prose.status_code,
        'properLegacyReadStatus': legacy_read.status_code, 'properLegacyCancelStatus': legacy_cancel.status_code,
        'realCalls': 0, 'paidCalls': 0}
    folder = os.environ.get('PROSE_TEST_OBSERVATIONS')
    if folder:
        target = Path(folder); target.mkdir(parents=True, exist_ok=True)
        (target / 'legacy-protocol-separation.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    assert after == before, evidence
    assert read.status_code == cancel.status_code == 404, evidence
    assert read.json()['code'] == cancel.json()['code'] == 'RESOURCE_NOT_FOUND'
    assert prose.status_code == 200 and not prose.json()['cancelRequested'], prose.text
    assert legacy_read.status_code == legacy_cancel.status_code == 200, evidence
    with SessionLocal() as db:
        assert db.get(Task, legacy_id).cancel_requested
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
