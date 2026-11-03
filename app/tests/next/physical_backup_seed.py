"""Seed public synthetic reader/world data in an already migrated, owned test DB."""
import io
import json
from datetime import timedelta
from pathlib import Path
import sys
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from app.main import app
from app.db import SessionLocal
from app.models import Task, UsageRecord, Idempotency, uid, now
from tests.helpers import BASE
from tests.test_cloud_playback import reader_body, synthetic_world

phase = sys.argv[1]
if phase == "seed":
    with TestClient(app) as client:
        registered = client.post(BASE + '/auth/register', json={'username': 'physical_fixture', 'password': 'synthetic-physical-password-123'})
        assert registered.status_code == 201
        login = client.post(BASE + '/auth/login', json={'username': 'physical_fixture', 'password': 'synthetic-physical-password-123', 'clientType': 'web', 'deviceName': 'owned-physical-fixture'})
        assert login.status_code == 200
        credentials = login.json(); client.headers['Authorization'] = 'Bearer ' + credentials['accessToken']
        response = client.post(BASE + '/books/demo', json={}, headers={'Idempotency-Key': 'physical-demo'})
        assert response.status_code == 201
        demo = response.json(); body = reader_body(client, demo)
        image = io.BytesIO(); Image.new('RGB', (120, 180), '#7d9874').save(image, 'PNG')
        scope = {'kind': 'book', 'bookId': demo['bookId'], 'branchId': demo['branchId'], 'revisionId': demo['revisionId']}
        uploaded = client.post(BASE + '/assets', files={'file': ('synthetic.png', image.getvalue(), 'image/png')},
            data={'purpose': 'cover', 'scope': json.dumps(scope), 'crop': json.dumps({'x': 0, 'y': 0, 'width': 120, 'height': 180}),
                  'licenseNote': 'Self-created synthetic test pixels', 'rightsConfirmed': 'true'},
            headers={'Idempotency-Key': 'physical-image'})
        assert uploaded.status_code == 201
        placed = client.post(BASE + '/asset-placements', json={'assetId': uploaded.json()['assetId'], 'scope': scope, 'purpose': 'cover'},
            headers={'Idempotency-Key': 'physical-cover-placement'})
        assert placed.status_code == 200, placed.text
        played = client.put(BASE + '/playback/position', json=body, headers={'Idempotency-Key': 'physical-playback'})
        assert played.status_code == 200
        revealed = client.post(BASE + '/stories/book-' + demo['bookId'] + '/compare/reveal', json={
            'confirmed': True, 'kind': 'book', 'resourceId': demo['bookId'], 'lineId': demo['branchId'],
            'nodeId': demo['revisionId'], 'expectedVersion': 0}, headers={'Idempotency-Key': 'physical-view-range'})
        assert revealed.status_code == 200
        world_body, _, _ = synthetic_world(credentials['user']['id'])
        played_world = client.put(BASE + '/playback/position', json=world_body, headers={'Idempotency-Key': 'physical-world-playback'})
        assert played_world.status_code == 200
    with SessionLocal() as db:
        for scope in ('physical-fixture-a', 'physical-fixture-b'):
            db.add(Idempotency(owner_id=credentials['user']['id'], scope=scope, key='same-key-different-scope',
                payload_hash='a' * 64, result={'syntheticScope': scope}, expires_at=now() + timedelta(days=1)))
        task = Task(id=uid(), owner_id=credentials['user']['id'], task_type='fixture_only', state='running',
            payload={}, persistence_state='pending', provider_state='not_started')
        db.add(task)
        if hasattr(UsageRecord, 'billing_metadata'):
            profile_id = uid()
            db.add(UsageRecord(owner_id=credentials['user']['id'], task_id=task.id,
                provider_call_id=uid(), profile_id=profile_id, consent_id=uid(), operation='fixture_only_usage',
                reserved_input=50, reserved_output=30, input_tokens=32, output_tokens=11,
                usage_state='reported', status='settled', latency_ms=23,
                billing_metadata={'story': {'id':'book-' + demo['bookId'], 'title':'合成物理备份测试'},
                    'profile': {'id':profile_id, 'name':'合成测试通道', 'modelId':'fixture-only', 'version':1},
                    'billing': {'reasoningTokens':7, 'reasoningIncludedInOutput':True,
                        'cost':0.125, 'currency':'USD', 'costState':'known'}}))
        db.commit()
    # A substantial synthetic binary validates streaming tar fingerprinting.
    path = Path('/data/private/synthetic-physical.bin')
    with path.open('wb') as output:
        for _ in range(512): output.write(bytes(range(256)) * 256)
    print(json.dumps({'seeded': True, 'realProviderCalls': 0, 'generatedSyntheticBytes': path.stat().st_size}))
elif phase in {'terminal', 'recoverable', 'cancel-own-fixture'}:
    with SessionLocal() as db:
        # These are our own synthetic guard states, never a provider request.
        count = db.scalar(text("SELECT count(*) FROM tasks WHERE task_type='fixture_only' AND provider_state!='not_started'"))
        assert count == 0
        state, persistence = ('failed', 'recoverable') if phase == 'recoverable' else ('succeeded', 'saved')
        db.execute(text("UPDATE tasks SET state=:state,persistence_state=:persistence WHERE task_type='fixture_only' AND provider_state='not_started'"), {'state': state, 'persistence': persistence})
        db.commit()
    print(json.dumps({'ownSyntheticTaskPhase': phase, 'realProviderCalls': 0}))
elif phase == 'quiet':
    with SessionLocal() as db:
        count = db.scalar(text("SELECT count(*) FROM tasks WHERE state NOT IN ('succeeded','failed','cancelled') OR persistence_state='recoverable'"))
        intents = db.scalar(text("SELECT count(*) FROM usage WHERE status='intent'"))
        schema = db.scalar(text('SELECT version_num FROM alembic_version'))
        world = db.scalar(text('SELECT max(version) FROM world.schema_version'))
    print(json.dumps({'nonterminalOrRecoverable': count, 'unsettledProviderIntents': intents, 'schema': schema, 'worldSchema': world}))
else:
    raise ValueError('Unknown synthetic seed phase')
