"""Multi-chapter jobs use separate bounded, durable generation/validation calls."""
from uuid import uuid4
from unittest.mock import patch
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.errors import Problem
from app.models import Task, Candidate, Revision, ContentBlock, Progress, UsageRecord
from app.providers import MockAdapter
from app.worker import process
from .helpers import BASE, read_chapter, observe, operation, accept


def payload_for(client, reader, demo, profile, count=3):
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    return {"operation": "counterfactual", "profileId": profile["id"], "anchor": anchor,
        "expectedHeadRevisionId": anchor["revisionId"], "instruction": "留下救人", "chapterCount": count}


def queue(client, profile, payload):
    planned = client.post(BASE + "/operation-plans", json=payload)
    assert planned.status_code == 201, planned.text
    plan = planned.json()
    approved = client.post(BASE + "/consents", json={"planId": plan["planId"], "planHash": plan["planHash"],
        "profileId": profile["id"], "maxCalls": plan["maxCalls"], "inputTokens": plan["inputTokens"], "outputTokens": plan["outputTokens"]})
    assert approved.status_code == 201, approved.text
    submitted = client.post(BASE + "/generation-tasks", json={**payload, "consentId": approved.json()["consentId"]},
        headers={"Idempotency-Key": uuid4().hex})
    assert submitted.status_code == 202, submitted.text
    return submitted.json()["taskId"], plan


@pytest.mark.parametrize("count", [0, 11, True, "3", 2.5])
def test_chapter_count_requires_bounded_integer(client, reader, demo, profile, count):
    payload = payload_for(client, reader, demo, profile, count)
    assert client.post(BASE + "/operation-plans", json=payload).status_code == 422


@pytest.mark.parametrize("count", [1, 3, 5, 10])
def test_plan_freezes_two_calls_per_requested_chapter(client, reader, demo, profile, count):
    payload = payload_for(client, reader, demo, profile, count)
    plan = client.post(BASE + "/operation-plans", json=payload).json()
    assert plan["chapterCount"] == count
    assert plan["maxCalls"] == plan["batchAnalysisBlocks"] + 2 * count
    assert plan["maxCalls"] <= 40
    assert plan["outputTokens"] == plan["maxCalls"] * 2048
    assert plan["inputTokens"] == plan["maxCalls"] * 12000


def test_authorization_for_one_chapter_cannot_start_three(client, reader, demo, profile):
    payload = payload_for(client, reader, demo, profile, 1)
    planned = client.post(BASE + "/operation-plans", json=payload).json()
    approved = client.post(BASE + "/consents", json={"planId": planned["planId"], "planHash": planned["planHash"],
        "profileId": profile["id"], "maxCalls": planned["maxCalls"], "inputTokens": planned["inputTokens"], "outputTokens": planned["outputTokens"]}).json()
    rejected = client.post(BASE + "/generation-tasks", json={**payload, "chapterCount": 3, "consentId": approved["consentId"]},
        headers={"Idempotency-Key": "count-substitution"})
    assert rejected.status_code == 403 and rejected.json()["code"] == "CONSENT_REQUIRED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_three_chapters_contiguous_context_atomic_accept_and_unread_boundary(client, reader, demo, profile):
    payload = payload_for(client, reader, demo, profile)
    task_id, plan = queue(client, profile, payload)
    original = MockAdapter.generate
    contexts = []
    def tracked(adapter, p, prompt, data, limit):
        if "chapter-v1" in prompt.splitlines()[0]:
            contexts.append(data)
        return original(adapter, p, prompt, data, limit)
    with patch.object(MockAdapter, "generate", tracked):
        process(task_id)
    task = client.get(BASE + "/tasks/" + task_id).json()
    assert task["state"] == "succeeded" and task["resultOutcome"] == "complete", task
    assert task["result"]["completedChapters"] == task["result"]["requestedChapters"] == 3
    candidate = client.get(BASE + "/candidates/" + task["result"]["candidateId"]).json()
    chapters = candidate["chapters"]
    assert len(chapters) == 3 and len({c["contentHash"] for c in chapters}) == 3
    assert contexts[1]["prefix"][0]["text"] == chapters[0]["text"]
    assert contexts[2]["prefix"][0]["text"] == chapters[1]["text"]
    assert contexts[2]["previousChapters"][0]["ending"] == chapters[0]["text"][-120:]
    committed = accept(client, task, key="three-accept")
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result["chapterCount"] == 3 and len(set(result["chapterIds"])) == 3
    assert accept(client, task, key="three-second-key").json() == result
    with SessionLocal() as db:
        original_revision = db.get(Revision, demo["revisionId"])
        revision = db.get(Revision, result["revisionId"])
        assert len(original_revision.manifest) == 4
        assert len(revision.manifest) == 4
        assert [c["title"] for c in revision.manifest[1:]] == [c["title"] for c in chapters]
        assert revision.manifest[0]["blocks"] == original_revision.manifest[0]["blocks"][:2]
        progress = db.scalar(select(Progress).where(Progress.branch_id == result["branchId"]))
        assert progress.max_observed_anchor["blockId"] in revision.manifest[0]["blocks"]
        assert progress.max_observed_anchor["blockId"] not in {b for c in revision.manifest[1:] for b in c["blocks"]}
        usage = list(db.scalars(select(UsageRecord).where(UsageRecord.task_id == task_id)))
        assert len(usage) == plan["maxCalls"]
        assert {u.operation for u in usage if not u.operation.startswith("analysis:")} == {
            "generation", "validation", "generation:2", "validation:2", "generation:3", "validation:3"}


@pytest.mark.parametrize("count", [5, 10])
def test_longer_requests_finish_exactly_requested_distinct_chapters(client, reader, demo, profile, count):
    task_id, plan = queue(client, profile, payload_for(client, reader, demo, profile, count))
    process(task_id)
    task = client.get(BASE + "/tasks/" + task_id).json()
    assert task["resultOutcome"] == "complete", task
    assert task["result"]["completedChapters"] == count
    candidate = client.get(BASE + "/candidates/" + task["result"]["candidateId"]).json()
    assert len(candidate["chapters"]) == len({c["contentHash"] for c in candidate["chapters"]}) == count
    committed = accept(client, task)
    assert committed.status_code == 200 and committed.json()["chapterCount"] == count
    with SessionLocal() as db:
        revision = db.get(Revision, committed.json()["revisionId"])
        assert len(revision.manifest) == count + 1
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id)) == plan["maxCalls"]


def test_three_chapters_append_once_and_second_baseline_conflicts(client, reader, demo, profile):
    first_id, _ = queue(client, profile, payload_for(client, reader, demo, profile, 1))
    process(first_id)
    first = client.get(BASE + "/tasks/" + first_id).json()
    committed = accept(client, first, key="initial-line").json()
    derived = {"bookId": demo["bookId"], "branchId": committed["branchId"], "revisionId": committed["revisionId"]}
    tail = read_chapter(client, derived, 1)["blocks"][-1]["endAnchor"]
    observe(client, reader, tail, expected=1, sequence=2)
    target = {"mode": "append_existing", "branchId": derived["branchId"], "name": "接着写"}
    one, _, _ = operation(client, profile, tail, "continue_at_tail", chapterCount=3, target=target)
    two, _, _ = operation(client, profile, tail, "continue_at_tail", chapterCount=3, target=target)
    assert one["resultOutcome"] == two["resultOutcome"] == "complete"
    appended = accept(client, one, target=target, key="append-three")
    assert appended.status_code == 200, appended.text
    assert appended.json()["branchId"] == derived["branchId"] and appended.json()["chapterCount"] == 3
    assert accept(client, one, target=target, key="append-three-repeat").json() == appended.json()
    assert accept(client, two, target=target, key="append-three-other").status_code == 409
    with SessionLocal() as db:
        before = db.get(Revision, derived["revisionId"])
        after = db.get(Revision, appended.json()["revisionId"])
        assert len(before.manifest) == 2 and len(after.manifest) == 5
        assert after.manifest[:2] == before.manifest


def test_cancel_after_first_chapter_preserves_checkpoint_without_candidate(client, reader, demo, profile):
    task_id, _ = queue(client, profile, payload_for(client, reader, demo, profile))
    original = MockAdapter.generate
    def cancelled(adapter, p, prompt, data, limit):
        if data.get("chapter", {}).get("index") == 2:
            assert client.post(BASE + "/tasks/" + task_id + "/cancel").status_code == 200
        return original(adapter, p, prompt, data, limit)
    with patch.object(MockAdapter, "generate", cancelled):
        process(task_id)
    task = client.get(BASE + "/tasks/" + task_id).json()
    assert task["state"] == "cancelled" and task["result"]["completedChapters"] == 1
    with SessionLocal() as db:
        assert len(db.get(Task, task_id).checkpoint["chapters"]) == 1
        assert db.scalar(select(func.count()).select_from(Candidate).where(Candidate.task_id == task_id)) == 0


def test_received_second_chapter_journals_resume_without_duplicate_provider_calls(client, reader, demo, profile):
    import app.worker as worker
    task_id, plan = queue(client, profile, payload_for(client, reader, demo, profile))
    original_event = worker.add_event
    def crash(db, task, kind, payload):
        if kind == "chapter_saved" and payload["completedChapters"] == 2:
            raise RuntimeError("simulated persistence interruption after both provider receipts")
        return original_event(db, task, kind, payload)
    with patch.object(worker, "add_event", crash):
        process(task_id)
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.state == "failed" and task.persistence_state == "recoverable"
        assert len(task.checkpoint["chapters"]) == 1
        before = db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id))
    original = MockAdapter.generate
    called = []
    def only_new(adapter, p, prompt, data, limit):
        called.append(data)
        assert data.get("chapter", {}).get("index") == 3 or prompt.startswith("validation-")
        return original(adapter, p, prompt, data, limit)
    with patch.object(MockAdapter, "generate", only_new):
        process(task_id)
    task = client.get(BASE + "/tasks/" + task_id).json()
    assert task["resultOutcome"] == "complete" and task["result"]["completedChapters"] == 3
    assert len(called) == 2
    with SessionLocal() as db:
        after = db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id))
        assert after == before + 2 == plan["maxCalls"]


def test_ambiguous_second_call_is_never_automatically_recharged(client, reader, demo, profile):
    task_id, _ = queue(client, profile, payload_for(client, reader, demo, profile))
    original = MockAdapter.generate
    def timeout(adapter, p, prompt, data, limit):
        if data.get("chapter", {}).get("index") == 2:
            raise Problem("PROVIDER_TIMEOUT", "simulated ambiguous transport timeout")
        return original(adapter, p, prompt, data, limit)
    with patch.object(MockAdapter, "generate", timeout):
        process(task_id)
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert len(task.checkpoint["chapters"]) == 1
        before = db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id))
        task.persistence_state = "recoverable"
        db.commit()
    with patch.object(MockAdapter, "generate", side_effect=AssertionError("must not resend ambiguous call")):
        process(task_id)
    task = client.get(BASE + "/tasks/" + task_id).json()
    assert task["errorCode"] == "PROVIDER_TIMEOUT"
    assert task["result"]["completedChapters"] == 1
    draft = client.get(BASE + "/candidates/" + task["result"]["candidateId"]).json()
    assert draft["text"] and draft["partial"] and not draft["canAccept"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id)) == before


def test_second_chapter_plot_warning_preserves_all_requested_chapters_and_reader_choice(client, reader, demo, profile):
    task_id, _ = queue(client, profile, payload_for(client, reader, demo, profile))
    original = MockAdapter.generate
    def unsafe(adapter, p, prompt, data, limit):
        result = original(adapter, p, prompt, data, limit)
        if data.get("chapter", {}).get("index") == 2:
            result["text"] += "林渡就是夜渡。"
        return result
    with patch.object(MockAdapter, "generate", unsafe):
        process(task_id)
    task_response = client.get(BASE + "/tasks/" + task_id)
    task = task_response.json()
    assert task["resultOutcome"] == "complete" and task["result"]["completedChapters"] == 3
    candidate = client.get(BASE + "/candidates/" + task["result"]["candidateId"]).json()
    assert '夜渡' in candidate['text'] and len(candidate['chapters']) == 3 and candidate['canAccept']
    assert candidate['reviewWarnings'] and candidate['reviewWarnings'][0]['chapterIndex'] == 2
    assert "夜渡" not in task_response.text
    assert accept(client, task).status_code == 200


def test_chapter_title_is_reviewed_but_warning_does_not_hide_the_chapter(client, reader, demo, profile):
    task_id, _ = queue(client, profile, payload_for(client, reader, demo, profile))
    original = MockAdapter.generate
    def hidden_title(adapter, p, prompt, data, limit):
        result = original(adapter, p, prompt, data, limit)
        if data.get("chapter", {}).get("index") == 2:
            _, _, body = result["text"].partition("\n")
            result["text"] = "标题：夜渡的真实身份\n" + body
        return result
    with patch.object(MockAdapter, "generate", hidden_title):
        process(task_id)
    task = client.get(BASE + "/tasks/" + task_id).json()
    assert task["resultOutcome"] == "complete" and task["result"]["completedChapters"] == 3
    candidate = client.get(BASE + "/candidates/" + task["result"]["candidateId"]).json()
    assert '夜渡的真实身份' in candidate['chapters'][1]['title'] and candidate['canAccept']
    assert candidate['reviewWarnings'] and candidate['reviewWarnings'][0]['chapterIndex'] == 2


def test_tampered_chapter_checkpoint_rejects_read_and_accept(client, reader, demo, profile):
    task_id, _ = queue(client, profile, payload_for(client, reader, demo, profile))
    process(task_id)
    task = client.get(BASE + "/tasks/" + task_id).json()
    with SessionLocal() as db:
        row = db.get(Task, task_id)
        chapters = row.checkpoint["chapters"]
        row.checkpoint = {**row.checkpoint, "chapters": [{**chapters[0], "text": "corrupted saved chapter"}, *chapters[1:]]}
        db.commit()
    assert client.get(BASE + "/candidates/" + task["result"]["candidateId"]).status_code == 409
    assert client.post(BASE + "/candidates/" + task["result"]["candidateId"] + "/accept",
        json={"expectedHeadRevisionId": demo["revisionId"], "target": {"mode": "new_child_branch", "name": "校验"}},
        headers={"Idempotency-Key": "invalid-chapter"}).status_code == 409


def test_legacy_single_chapter_payload_remains_valid(client, reader, demo, profile):
    payload = payload_for(client, reader, demo, profile)
    del payload["chapterCount"]
    task_id, plan = queue(client, profile, payload)
    assert plan["chapterCount"] == 1
    process(task_id)
    task = client.get(BASE + "/tasks/" + task_id).json()
    assert task["resultOutcome"] == "complete" and task["result"]["completedChapters"] == 1
    candidate = client.get(BASE + "/candidates/" + task["result"]["candidateId"]).json()
    assert candidate["text"] == candidate["chapters"][0]["text"]
    assert accept(client, task).status_code == 200


def test_cancellation_committed_after_fast_check_is_rechecked_under_provider_intent_lock(client, reader, demo, profile):
    from app.worker import call_provider
    task_id, _ = queue(client, profile, payload_for(client, reader, demo, profile, 1))
    def cancellation_arrives(_, **kwargs):
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.cancel_requested = True
            db.commit()
    with patch("app.worker.check_cancel", side_effect=cancellation_arrives), patch.object(MockAdapter, "generate", side_effect=AssertionError("cancelled call must not send")):
        with pytest.raises(Problem) as rejected:
            call_provider(task_id, "generation", "counterfactual", {}, 2048)
    assert rejected.value.code == "TASK_CANCELLED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id)) == 0
