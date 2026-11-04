"""P0-3/P0-4: pending placeholder streaming, regenerate and edit-resend."""
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Task
from app.play_controls import chat
from app.play_controls.models import WorldChatMessage
from app.world.worker import process
from .test_play_controls_world_chat import API, create_chat, world
from .test_authoring_publications import post


def send(client, session, message, turn, key):
    result = client.post(API + '/world-chats/' + session + '/send',
        json={'message': message, 'turnId': turn}, headers={'Idempotency-Key': key})
    assert result.status_code == 202, result.text
    return result.json()


def messages(client, session):
    return client.get(API + '/world-chats/' + session).json()['messages']


def regenerate(client, session, key):
    return client.post(API + '/world-chats/' + session + '/regenerate', headers={'Idempotency-Key': key})


def edit(client, session, sequence, text, key):
    return client.patch(API + '/world-chats/' + session + '/messages/' + str(sequence),
        json={'text': text}, headers={'Idempotency-Key': key})


def test_send_inserts_pending_assistant_placeholder(client, reader, profile):
    w, line = world(client)
    session = create_chat(client, w, line)['id']
    sent = send(client, session, '你好', 'turn-one', 'one')
    current = client.get(API + '/world-chats/' + session).json()
    assert current['pendingTaskId'] == sent['taskId']
    rows = current['messages']
    assert [row['role'] for row in rows] == ['user', 'assistant']
    assert rows[0]['text'] == '你好' and rows[0]['status'] == 'saved'
    assert rows[0]['turnId'] == 'turn-one'
    assert rows[1]['status'] == 'pending' and rows[1]['text'] == ''
    assert rows[1]['turnId'] == 'turn-one' and rows[1]['taskId'] == sent['taskId']


def test_partial_write_is_throttled_and_stream_updates_placeholder(client, reader, profile, monkeypatch):
    w, line = world(client)
    session = create_chat(client, w, line)['id']
    sent = send(client, session, '继续', 'turn-partial', 'partial')
    with SessionLocal() as db:
        task = db.get(Task, sent['taskId'])
        row = db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id == session,
            WorldChatMessage.role == 'assistant', WorldChatMessage.turn_key == 'turn-partial'))
        assert row.status == 'pending' and row.text == ''
        # First mirror is immediate; the next one is inside the 600ms/40 char window.
        chat.write_partial(db, task, '甲' * 45)
        assert row.text == '甲' * 45
        chat.write_partial(db, task, '甲' * 46)
        assert row.text == '甲' * 45
        # Final publication always wins.
        chat.write_partial(db, task, '乙' * 46, final=True)
        assert row.text == '乙' * 46
    seen = []
    real = chat.write_partial

    def spy(db, task, text, *, final=False):
        seen.append((final, text))
        return real(db, task, text, final=final)

    monkeypatch.setattr(chat, 'write_partial', spy)
    process(sent['taskId'])
    assert any(not final and text.strip() for final, text in seen), 'stream never reached the placeholder'
    assert any(final and text.strip() for final, text in seen)
    rows = messages(client, session)
    assert [row['status'] for row in rows] == ['saved', 'saved']
    assert rows[1]['text'].strip()
    assert sent['taskId'] not in chat._partial_writes


def test_regenerate_replaces_last_assistant_and_reuses_turn(client, reader, profile):
    w, line = world(client)
    session = create_chat(client, w, line)['id']
    first = send(client, session, '第一轮', 'turn-one', 'one')
    process(first['taskId'])
    first_rows = messages(client, session)
    assert [row['status'] for row in first_rows] == ['saved', 'saved']
    replay = regenerate(client, session, 'regen-one')
    assert replay.status_code == 202, replay.text
    task_id = replay.json()['taskId']
    assert task_id != first['taskId']
    pending = messages(client, session)
    assert [row['role'] for row in pending] == ['user', 'assistant']
    assert pending[1]['status'] == 'pending' and pending[1]['text'] == ''
    assert pending[0]['turnId'] == 'turn-one'
    with SessionLocal() as db:
        user_row = db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id == session,
            WorldChatMessage.role == 'user', WorldChatMessage.turn_key == 'turn-one'))
        assert user_row.task_id == task_id
        assert db.get(Task, task_id).state in ('queued', 'preparing', 'running')
    process(task_id)
    final_rows = messages(client, session)
    assert [row['status'] for row in final_rows] == ['saved', 'saved']
    assert final_rows[1]['taskId'] == task_id and final_rows[1]['text'].strip()
    assert client.get(API + '/world-chats/' + session).json()['pendingTaskId'] is None


def test_regenerate_and_edit_reject_pending_and_empty(client, reader, profile):
    w, line = world(client)
    session = create_chat(client, w, line)['id']
    nothing = regenerate(client, session, 'regen-empty')
    assert nothing.status_code == 409 and nothing.json()['code'] == 'NOTHING_TO_REGENERATE'
    sent = send(client, session, '先来一句', 'turn-one', 'one')
    busy = regenerate(client, session, 'regen-busy')
    assert busy.status_code == 409 and busy.json()['code'] == 'CHAT_PENDING'
    process(sent['taskId'])
    blank = edit(client, session, 1, '   ', 'edit-blank')
    assert blank.status_code == 422 and blank.json()['code'] == 'MESSAGE_REQUIRED'
    assistant = edit(client, session, 2, '不能改回复', 'edit-assistant')
    assert assistant.status_code == 422 and assistant.json()['code'] == 'MESSAGE_NOT_EDITABLE'
    missing = edit(client, session, 99, '不存在', 'edit-missing')
    assert missing.status_code == 404
    send(client, session, '第二轮', 'turn-two', 'two')
    busy_edit = edit(client, session, 3, '等待中不能编辑', 'edit-busy')
    assert busy_edit.status_code == 409 and busy_edit.json()['code'] == 'CHAT_PENDING'


def test_edit_user_message_truncates_later_turns_and_requeues(client, reader, profile):
    w, line = world(client)
    session = create_chat(client, w, line)['id']
    first = send(client, session, '第一句', 'turn-one', 'one')
    process(first['taskId'])
    second = send(client, session, '第二句', 'turn-two', 'two')
    process(second['taskId'])
    assert [row['role'] for row in messages(client, session)] == ['user', 'assistant', 'user', 'assistant']
    edited = edit(client, session, 1, '改过的第一句', 'edit-first')
    assert edited.status_code == 202, edited.text
    body = edited.json()
    assert body['sequence'] == 1
    rows = messages(client, session)
    assert [row['sequence'] for row in rows] == [1, 2]
    assert rows[0]['text'] == '改过的第一句' and rows[0]['status'] == 'saved'
    assert rows[1]['status'] == 'pending' and rows[1]['turnId'] == 'turn-one'
    with SessionLocal() as db:
        user_row = db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id == session,
            WorldChatMessage.sequence == 1))
        assert user_row.task_id == body['taskId']
    process(body['taskId'])
    done = messages(client, session)
    assert [row['status'] for row in done] == ['saved', 'saved']
    assert done[1]['taskId'] == body['taskId'] and done[1]['text'].strip()
    assert client.get(API + '/world-chats/' + session).json()['pendingTaskId'] is None
