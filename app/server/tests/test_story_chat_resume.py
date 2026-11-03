"""Saved chat navigation competes only with valid, actually saved messages."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select, func

from app.db import SessionLocal
from app.models import ResumeTarget, RoleMessage, RoleSession, Task, UsageRecord, now
from app.world.models import WorldLine
from .helpers import BASE
from .test_stories import publication_scope_fixture, put, stored_boundary
from .test_world_security import isolated_storage_and_mock_only


def scenario(client, reader, demo, profile, root_kind="world"):
    world_root, allowed, denied, sessions, baseline = publication_scope_fixture(client, reader, demo, profile)
    root = world_root if root_kind == "world" else "book-" + allowed[0]["bookId"]
    # The scope fixture intentionally has future-dated rows for ordering tests.
    # Here every previous conversation is genuinely older than the new resume.
    with SessionLocal() as db:
        old = now() - timedelta(days=2)
        for session in db.scalars(select(RoleSession)):
            session.created_at = old
        for message in db.scalars(select(RoleMessage)):
            message.created_at = old
        db.commit()
    contexts = [{**deepcopy(context), "storyId": root, "mode": "chat", "entryOrigin": "conversation",
                 "characterId": "scope-person", "sessionId": sessions[context["readerBranchId"]]}
                for context in allowed + denied]
    assert put(client, contexts[0]).status_code == 200
    return root, contexts, baseline


def resume(client, root):
    response = client.get(BASE + "/stories/" + root)
    assert response.status_code == 200, response.text
    return response.json()["resume"]["chat"]


def message(owner, session_id, sequence=1, status="saved", role="user", text="Own synthetic cross-device conversation"):
    with SessionLocal() as db:
        row = RoleMessage(owner_id=owner, session_id=session_id, turn_id="chat-navigation-" + str(sequence),
            sequence=sequence, role=role, text=text, task_id="0"*32,
            status=status)
        db.add(row); db.commit()
        return row.created_at


def unchanged(demo, baseline):
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == baseline["tasks"]
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == baseline["usage"]
        assert {line.id: line.head_checkpoint_id for line in db.scalars(select(WorldLine))} == baseline["heads"]
        assert stored_boundary(db, demo) == baseline["progress"]


@pytest.mark.parametrize("root_kind", ["book", "world"])
def test_new_actual_message_beats_saved_resume_but_unused_session_does_not(client, reader, demo, profile, root_kind):
    root, contexts, baseline = scenario(client, reader, demo, profile, root_kind)
    a, b = contexts[:2]
    with SessionLocal() as db:
        original = db.get(RoleSession, b["sessionId"])
        unused = RoleSession(owner_id=original.owner_id, book_id=original.book_id, branch_id=original.branch_id,
            pinned_revision_id=original.pinned_revision_id, pinned_anchor=deepcopy(original.pinned_anchor),
            character_id=original.character_id, character_name=original.character_name, snapshot_id=original.snapshot_id)
        db.add(unused); db.commit(); unused_id = unused.id
    assert resume(client, root)["context"] == a
    b = {**b, "sessionId": unused_id}
    instant = message(reader["user"]["id"], unused_id)
    latest = resume(client, root)
    assert latest["version"] == 1 and latest["context"] == b
    assert latest["updatedAt"] == instant.isoformat()
    # Explicitly opening A after that message remains authoritative until an
    # actual later message, rather than a mere session timestamp, occurs.
    assert put(client, a, 1).status_code == 200
    assert resume(client, root)["context"] == a
    message(reader["user"]["id"], unused_id, 2)
    assert resume(client, root)["context"] == b
    with SessionLocal() as db:
        row = db.scalar(select(ResumeTarget).where(ResumeTarget.story_id == root))
        assert row.version == 2 and row.context == a
    unchanged(demo, baseline)


@pytest.mark.parametrize("invalid", ["archived", "deleted", "invalid-pin", "other-publication", "unrelated-branch", "unsaved-message"])
def test_new_message_from_unavailable_or_foreign_world_scope_cannot_hide_saved_chat(client, reader, demo, profile, invalid):
    root, contexts, baseline = scenario(client, reader, demo, profile)
    saved = contexts[0]
    other = contexts[2] if invalid == "other-publication" else contexts[3] if invalid == "unrelated-branch" else contexts[1]
    sid = other["sessionId"]
    with SessionLocal() as db:
        # The deliberately existing W2 message belongs to another turn/seq.
        sequence = db.scalar(select(func.max(RoleMessage.sequence)).where(RoleMessage.session_id == sid)) or 0
    message(reader["user"]["id"], sid, sequence + 1, "draft" if invalid == "unsaved-message" else "saved")
    with SessionLocal() as db:
        session = db.get(RoleSession, sid)
        if invalid in {"archived", "deleted"}:
            session.status = invalid
        elif invalid == "invalid-pin":
            session.pinned_revision_id = "f"*32
        db.commit()
    latest = resume(client, root)
    assert latest["version"] == 1 and latest["context"] == saved
    unchanged(demo, baseline)


def test_real_chat_activity_outweighs_unused_conversation_in_fallback(client, reader, demo, profile):
    root, contexts, baseline = scenario(client, reader, demo, profile)
    a, b = contexts[:2]
    message(reader["user"]["id"], a["sessionId"])
    with SessionLocal() as db:
        db.delete(db.scalar(select(ResumeTarget).where(ResumeTarget.story_id == root)))
        db.get(RoleSession, b["sessionId"]).created_at = now()
        db.commit()
    latest = resume(client, root)
    assert latest["version"] == 0 and latest["context"] == a
    unchanged(demo, baseline)


@pytest.mark.parametrize("status,role", [("pending", "user"), ("review_warning", "assistant"),
    ("interrupted", "assistant"), ("stopped", "assistant")])
def test_real_durable_warning_partial_stopped_or_pending_user_message_is_activity(client, reader, demo, profile, status, role):
    root, contexts, baseline = scenario(client, reader, demo, profile)
    b = contexts[1]
    instant = message(reader["user"]["id"], b["sessionId"], status=status, role=role)
    latest = resume(client, root)
    assert latest["version"] == 1 and latest["context"] == b
    assert latest["updatedAt"] == instant.isoformat()
    unchanged(demo, baseline)


@pytest.mark.parametrize("status,role,text", [("unknown", "assistant", "Unknown status is not a saved reply"),
    ("pending", "assistant", "Pending assistant placeholder is not a received reply"),
    ("saved", "assistant", " \t\n")])
def test_unknown_or_unreceived_assistant_placeholder_is_not_chat_activity(client, reader, demo, profile, status, role, text):
    root, contexts, baseline = scenario(client, reader, demo, profile)
    message(reader["user"]["id"], contexts[1]["sessionId"], status=status, role=role, text=text)
    assert resume(client, root)["context"] == contexts[0]
    unchanged(demo, baseline)
