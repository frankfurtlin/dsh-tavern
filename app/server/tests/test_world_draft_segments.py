import json
from unittest.mock import patch
from app.db import SessionLocal
from app.domain import digest
from app.models import Task
from app.world.models import WorldLine, WorldTurn
from app.world.schemas import DirectorOutput
from app.world.worker import process
from sqlalchemy import select
from .test_scenes import scene_world
from .test_scene_interaction import executable_scene_source, body, API


def test_rejected_review_preserves_exact_received_dialogue_channels_and_rejects_stale_channels(client, scene_world, profile):
    w = scene_world
    response = client.post(API+w['root']+'/actions', json=body(w, '观察窗外'), headers={'Idempotency-Key':'draft-segments'})
    assert response.status_code == 202, response.text
    reply = json.dumps({'kind':'narration', 'text':'风拂过窗前，她闭上了眼睛。'}, ensure_ascii=False)
    def call(task, stage, prompt, data, limit):
        if prompt == 'world_director':
            return {'text':json.dumps(DirectorOutput(title='窗前', elapsedMinutes=1, timeLabel='清晨', publicOutcome='风拂过窗前。').model_dump()), 'finishReason':'stop'}
        if prompt == 'world_narrator': return {'text':reply, 'finishReason':'stop'}
        return {'text':'{"consistent":false,"issues":["文字与结果有出入"]}', 'finishReason':'stop'}
    with patch('app.world.worker.call_provider', side_effect=call): process(response.json()['taskId'])
    with SessionLocal() as db:
        task = db.get(Task, response.json()['taskId'])
        turn = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task.id))
        assert task.state == 'failed'
        assert db.get(WorldLine, w['line']).head_checkpoint_id == w['cp']
        assert turn.result['draftTextHash'] == digest(turn.draft_text)
        turn_id = turn.id
    result = client.get(API+f"/worlds/{w['world']}/turns/{turn_id}").json()
    assert result['draftSegments'] == [{'kind':'narration','text':'风拂过窗前，她闭上了眼睛。'}]
    with SessionLocal() as db:
        db.get(WorldTurn, turn_id).draft_text = '这是随后收到的另一个片段。'
        db.commit()
    assert client.get(API+f"/worlds/{w['world']}/turns/{turn_id}").json()['draftSegments'] is None
