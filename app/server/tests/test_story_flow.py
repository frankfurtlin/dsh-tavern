"""Single-action jobs retain idempotency, finite authorization and read boundaries."""
from sqlalchemy import select, func
from app.models import Task, Progress, UsageRecord, ModelProfile, Consent, OperationPlan, KnowledgeSnapshot, AnalysisChunk
from app.db import SessionLocal
from app.worker import process
from app.providers import MockAdapter
from unittest.mock import patch
from .helpers import BASE, read_chapter, observe, operation


def start(client, reader, demo, profile, count=3, key="start"):
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    body = {"operation": "counterfactual", "profileId": profile["id"], "anchor": anchor,
        "expectedHeadRevisionId": anchor["revisionId"], "instruction": "留下救人", "chapterCount": count}
    response = client.post(BASE+"/story-runs", json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json(), body


def finish(run):
    for _ in range(12):
        process(run["id"])
        with SessionLocal() as db:
            parent = db.get(Task, run["id"])
            child_id = parent.checkpoint.get("currentTaskId")
            state = parent.state
        if state in {"succeeded", "failed", "cancelled"}:
            break
        if child_id:
            process(child_id)


def test_single_action_three_chapters_and_same_request_retry(client, reader, demo, profile):
    run, body = start(client, reader, demo, profile)
    duplicate = client.post(BASE+"/story-runs", json=body, headers={"Idempotency-Key": "start"})
    assert duplicate.json()["id"] == run["id"]
    finish(run)
    result = client.get(BASE+"/story-runs/"+run["id"]).json()
    assert result["state"] == "succeeded", result
    assert result["requestedChapters"] == result["completedChapters"] == 3
    candidate = client.get(BASE+"/candidates/"+result["candidateId"]).json()
    assert len(candidate["chapters"]) == 3
    with SessionLocal() as db:
        calls = db.scalar(select(func.count()).select_from(UsageRecord))
    finish(run)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == calls
    listed = client.get(BASE+"/story-runs", params={"branchId": body["anchor"]["branchId"]}).json()
    assert listed["items"][0]["id"] == run["id"]


def test_cancel_parent_cancels_child_without_calls(client, reader, demo, profile):
    run, _ = start(client, reader, demo, profile)
    process(run["id"])
    client.post(BASE+"/story-runs/"+run["id"]+"/cancel")
    finish(run)
    assert client.get(BASE+"/story-runs/"+run["id"]).json()["state"] == "cancelled"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_recoverable_child_keeps_parent_pending_until_same_child_recovers(client, reader, demo, profile):
    run, _ = start(client, reader, demo, profile)
    process(run["id"])
    with SessionLocal() as db:
        parent = db.get(Task, run["id"])
        child_id = parent.checkpoint["currentTaskId"]
        child = db.get(Task, child_id)
        child.state, child.persistence_state = "failed", "recoverable"
        db.commit()
    process(run["id"])
    pending = client.get(BASE+"/story-runs/"+run["id"]).json()
    assert pending["state"] == "preparing" and pending["phase"] == "recovering"
    assert pending["currentTaskId"] == child_id
    with SessionLocal() as db:
        assert db.get(Task, run["id"]).checkpoint["children"] == [child_id]
    finish(run)
    assert client.get(BASE+"/story-runs/"+run["id"]).json()["state"] == "succeeded"


def test_changed_model_keeps_queued_parent_and_children_on_frozen_profile(client, reader, demo, profile,monkeypatch):
    seen=[];original=MockAdapter.generate
    def capture(self,p,prompt,data,limit):
        seen.append((p.model_id,p.version))
        return original(self,p,prompt,data,limit)
    monkeypatch.setattr(MockAdapter,'generate',capture)
    run, _ = start(client, reader, demo, profile)
    with SessionLocal() as db:
        p = db.get(ModelProfile, profile["id"])
        p.version += 1
        p.model_id='edited-after-queue'
        db.commit()
    finish(run)
    result = client.get(BASE+"/story-runs/"+run["id"]).json()
    assert result["state"] == "succeeded" and result['completedChapters']==3
    assert seen and all(value==('fixture-v1',1) for value in seen)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == len(seen)


def test_timeline_masks_unread_titles_and_paginates(client, reader, demo):
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    response = client.get(BASE+"/books/"+demo["bookId"]+"/timeline", params={"chapterLimit": 2})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["originalBranchId"] == demo["branchId"]
    chapters = data["branches"][0]["chapters"]
    assert chapters[0]["position"] == 0 and chapters[0]["observed"]
    assert chapters[1]["title"] == "第 2 章" and not chapters[1]["observed"] and chapters[1]["readable"]
    assert data["branches"][0]["nextChapterOffset"] == 2
    second = client.get(BASE+"/books/"+demo["bookId"]+"/timeline", params={"chapterOffset": 2, "chapterLimit": 2}).json()
    assert second["branches"][0]["chapters"][0]["position"] == 2
    assert second["branches"][0]["nextChapterOffset"] is None


def test_story_flow_owner_isolation(client, reader, demo, profile):
    run, body = start(client, reader, demo, profile)
    client.post(BASE+"/auth/register", json={"username": "second_reader", "password": "reader-password-123"})
    login = client.post(BASE+"/auth/login", json={"username": "second_reader", "password": "reader-password-123"}).json()
    client.headers["Authorization"] = "Bearer "+login["accessToken"]
    assert client.get(BASE+"/story-runs/"+run["id"]).status_code == 404
    assert client.post(BASE+"/story-runs/"+run["id"]+"/cancel").status_code == 404
    assert client.get(BASE+"/books/"+demo["bookId"]+"/timeline").status_code == 404


def test_child_checks_parent_cancel_before_provider_stage(client, reader, demo, profile):
    run, _ = start(client, reader, demo, profile)
    process(run["id"])
    with SessionLocal() as db:
        parent = db.get(Task, run["id"])
        child_id = parent.checkpoint["currentTaskId"]
        parent.cancel_requested = True
        db.commit()
    process(child_id)
    with SessionLocal() as db:
        child = db.get(Task, child_id)
        assert child.state == "cancelled"
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == child_id)) == 0


def test_rejected_child_is_not_a_successful_story_run(client, reader, demo, profile):
    run, _ = start(client, reader, demo, profile)
    original = MockAdapter.generate
    def short(adapter, p, prompt, data, limit):
        if "continuation" in prompt.splitlines()[0] or "counterfactual" in prompt.splitlines()[0]:
            return {"text": "太短。", "finishReason": "stop", "usage": {"inputTokens": 20, "outputTokens": 3, "state": "known"}}
        return original(adapter, p, prompt, data, limit)
    with patch.object(MockAdapter, "generate", short):
        finish(run)
    result = client.get(BASE+"/story-runs/"+run["id"]).json()
    assert result["state"] == "failed" and result["phase"] == "rejected", result
    assert result["completedChapters"] == 0


def test_long_context_automatically_batches_before_generation(client, reader, profile):
    source = ("第一章 航标\n"+"海风经过空港。"*1500).encode("utf-8")
    imported = client.post(BASE+"/books/imports", files={"file": ("航标.txt", source, "text/plain")}, headers={"Idempotency-Key": "long-import"})
    assert imported.status_code == 202, imported.text
    import_id = imported.json()["importTaskId"]
    process(import_id)
    confirmed = client.post(BASE+"/books/imports/"+import_id+"/confirm", json={"title": "航标"}, headers={"Idempotency-Key": "long-confirm"})
    assert confirmed.status_code == 201, confirmed.text
    book = confirmed.json()
    anchor = read_chapter(client, book)["blocks"][-1]["endAnchor"]
    observe(client, reader, anchor)
    response = client.post(BASE+"/story-runs", json={"operation": "continue_at_tail", "profileId": profile["id"], "anchor": anchor,
        "expectedHeadRevisionId": anchor["revisionId"]}, headers={"Idempotency-Key": "long-story"})
    assert response.status_code == 202, response.text
    finish(response.json())
    result = client.get(BASE+"/story-runs/"+response.json()["id"]).json()
    assert result["state"] == "succeeded", result
    with SessionLocal() as db:
        parent = db.get(Task, result["id"])
        children = [db.get(Task, child) for child in parent.checkpoint["children"]]
        assert [child.task_type for child in children] == ["analysis", "generation"]
        assert parent.checkpoint["authorizedCalls"] == parent.payload["budget"]["maxCalls"]


def limited_body(client, reader, demo, profile):
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    return {"operation": "counterfactual", "profileId": profile["id"], "anchor": anchor,
        "expectedHeadRevisionId": anchor["revisionId"], "instruction": "留下救人", "chapterCount": 1, "maxCalls": 2}


def test_uncached_two_call_limit_rejects_before_task_consent_or_usage(client, reader, demo, profile):
    body = limited_body(client, reader, demo, profile)
    response = client.post(BASE + "/story-runs", json=body, headers={"Idempotency-Key": "uncached-cap-two"})
    assert response.status_code == 403 and response.json()["code"] == "BUDGET_NOT_AUTHORIZED"
    assert response.json()["details"] == {"requiredCalls": 3, "maxCalls": 2}
    with SessionLocal() as db:
        for model in (Task, OperationPlan, Consent, UsageRecord):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        row = db.get(ModelProfile, profile["id"])
        assert row.calls_reserved == row.input_reserved == row.output_reserved == 0


def test_cached_two_call_limit_is_frozen_and_same_key_cannot_change_cap(client, reader, demo, profile):
    body = limited_body(client, reader, demo, profile)
    analyzed, _, _ = operation(client, profile, body["anchor"], "analysis", instruction="")
    assert analyzed["state"] == "succeeded"
    with SessionLocal() as db:
        before_calls = db.get(ModelProfile, profile["id"]).calls_reserved
    response = client.post(BASE + "/story-runs", json=body, headers={"Idempotency-Key": "cached-cap-two"})
    assert response.status_code == 202, response.text
    run = response.json()
    with SessionLocal() as db:
        parent = db.get(Task, run["id"])
        assert parent.payload["budget"]["maxCalls"] == parent.payload["callLimit"] == 2
        assert "maxCalls" not in parent.payload["operation"]
    duplicate = client.post(BASE + "/story-runs", json=body, headers={"Idempotency-Key": "cached-cap-two"})
    assert duplicate.json()["id"] == run["id"]
    changed = client.post(BASE + "/story-runs", json={**body, "maxCalls": 3}, headers={"Idempotency-Key": "cached-cap-two"})
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_CONFLICT"
    finish(run)
    assert client.get(BASE + "/story-runs/" + run["id"]).json()["state"] == "succeeded"
    with SessionLocal() as db:
        parent = db.get(Task, run["id"])
        assert parent.checkpoint["authorizedCalls"] == parent.payload["budget"]["maxCalls"] == 2
        child_ids = parent.checkpoint["children"]
        assert len(child_ids) == 1 and db.get(Task, child_ids[0]).task_type == "generation"
        assert db.scalar(select(Consent).where(Consent.task_id == child_ids[0])).max_calls == 2
        row = db.get(ModelProfile, profile["id"])
        assert row.calls_reserved - before_calls == 2 and row.call_budget == profile["callBudget"]


def test_cache_loss_after_creation_cannot_expand_frozen_two_call_parent(client, reader, demo, profile):
    body = limited_body(client, reader, demo, profile)
    analyzed, _, _ = operation(client, profile, body["anchor"], "analysis", instruction="")
    assert analyzed["state"] == "succeeded"
    response = client.post(BASE + "/story-runs", json=body, headers={"Idempotency-Key": "cache-loss-cap-two"})
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        db.query(KnowledgeSnapshot).delete()
        db.query(AnalysisChunk).delete()
        before = {model: db.scalar(select(func.count()).select_from(model)) for model in (Task, OperationPlan, Consent, UsageRecord)}
        db.commit()
    finish(response.json())
    result = client.get(BASE + "/story-runs/" + response.json()["id"]).json()
    assert result["state"] == "failed" and result["errorCode"] == "BUDGET_NOT_AUTHORIZED"
    with SessionLocal() as db:
        parent = db.get(Task, response.json()["id"])
        assert parent.payload["budget"]["maxCalls"] == 2 and parent.checkpoint["children"] == []
        assert parent.checkpoint["authorizedCalls"] == 0
        for model, count in before.items():
            assert db.scalar(select(func.count()).select_from(model)) == count


def test_call_limit_is_a_strict_integer_not_boolean(client, reader, demo, profile):
    body = limited_body(client, reader, demo, profile)
    response = client.post(BASE + "/story-runs", json={**body, "maxCalls": True}, headers={"Idempotency-Key": "boolean-call-cap"})
    assert response.status_code == 422 and response.json()["code"] == "INVALID_REQUEST"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_no_call_ceiling_keeps_preexisting_request_identity(client, reader, demo, profile):
    from app.schemas import Operation
    from app.models import Idempotency
    from app.domain import digest, canonical
    body = limited_body(client, reader, demo, profile)
    del body["maxCalls"]
    response = client.post(BASE + "/story-runs", json=body, headers={"Idempotency-Key": "old-no-cap-request"})
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        request_record = db.scalar(select(Idempotency).where(Idempotency.scope == "story-run", Idempotency.key == "old-no-cap-request"))
        assert request_record.payload_hash == digest(canonical(Operation.model_validate(body).model_dump()))
    repeated = client.post(BASE + "/story-runs", json={**body, "maxCalls": None}, headers={"Idempotency-Key": "old-no-cap-request"})
    assert repeated.status_code == 202 and repeated.json()["id"] == response.json()["id"]
