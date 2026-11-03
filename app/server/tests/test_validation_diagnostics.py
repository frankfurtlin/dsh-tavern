"""Novel reviews advise readers while role conversations retain their own contract."""
import json
from unittest.mock import patch
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Task, Candidate, Branch, UsageRecord
from app.providers import MockAdapter
from app.validation_gate import inspect_verdict
from .helpers import BASE
from .test_story_flow import start, finish


def verdict(code="SAFE", evidence=(), decision="pass", severity="none", finish="stop"):
    return {"text": json.dumps({"verdict": decision, "reasonCode": code,
            "evidenceIds": list(evidence), "severity": severity}), "finishReason": finish}


@pytest.mark.parametrize("result,expected", [
    (verdict(evidence=["known"]), "passed"),
    (verdict("SPOILER", ["hidden", "known"], "blocked", "P0"), "blocked"),
    (verdict("HISTORY_CONFLICT", ["known"], "blocked", "P1"), "blocked"),
    (verdict("UNCERTAIN", [], "unknown"), "unknown"),
    (verdict(evidence=["invented"]), "unknown"),
    (verdict("SPOILER", ["invented"], "blocked", "P0"), "unknown"),
    (verdict(finish="length"), "unknown"),
    (verdict(severity="P0"), "blocked"),
    ({"text": "PRIVATE-RESPONSE-not-json", "finishReason": "stop"}, "unknown"),
    ({"text": '{}', "finishReason": "stop"}, "unknown"),
])
def test_only_complete_supported_safe_verdict_can_pass(result, expected):
    diagnostic = inspect_verdict(result, {"known"}, {"hidden"})
    assert diagnostic["status"] == expected
    assert not any(value in json.dumps(diagnostic) for value in ["PRIVATE-RESPONSE", '"hidden"', '"known"', '"invented"'])


@pytest.mark.parametrize("code,hidden_ref,error_code", [
    ("SPOILER", True, "GENERATION_SPOILER"),
    ("HISTORY_CONFLICT", False, "GENERATION_HISTORY_CONFLICT"),
    ("UNCERTAIN", False, "VALIDATION_UNCERTAIN"),
])
def test_story_review_retains_prose_and_reader_can_accept(client, reader, demo, profile, code, hidden_ref, error_code):
    run, _ = start(client, reader, demo, profile, count=1)
    original = MockAdapter.generate
    def rejected(adapter, p, prompt, data, limit):
        if not prompt.startswith("validation-"):
            return original(adapter, p, prompt, data, limit)
        ids = [data["hiddenOriginal"][0]["id"]] if hidden_ref else [data["evidence"][0]["id"]]
        answer = verdict(code, ids, "unknown" if code == "UNCERTAIN" else "blocked", "none" if code == "UNCERTAIN" else "P0")
        return {**answer, "usage": {"inputTokens": 20, "outputTokens": 30, "state": "known"}}
    with patch.object(MockAdapter, "generate", rejected), patch("app.worker.hidden_evidence", return_value=[{"id": "private-hidden-block", "text": "PRIVATE-UNREAD-FUTURE"}]):
        finish(run)
    result = client.get(BASE+"/story-runs/"+run["id"]).json()
    assert result["state"] == "succeeded" and result["errorCode"] is None and result["warningCount"] == 1
    candidate = client.get(BASE+"/candidates/"+result["candidateId"]).json()
    assert candidate["text"] and len(candidate["chapters"]) == 1 and candidate["outcome"] == "complete" and candidate["canAccept"]
    assert candidate['reviewWarnings'] == [{'code': code, 'chapterIndex': 1}]
    assert 'PRIVATE-UNREAD-FUTURE' not in json.dumps(candidate)
    with SessionLocal() as db:
        child = db.get(Task, result["currentTaskId"])
        decision = child.checkpoint["validationDecision"]
        assert decision["reasonCode"] == code
        assert db.scalar(select(func.count()).select_from(Branch)) == 1
        before = db.scalar(select(func.count()).select_from(UsageRecord))
    finish(run)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before
        assert db.scalar(select(Candidate).where(Candidate.task_id == child.id)).status == "pending"
    accepted = client.post(BASE+"/candidates/"+candidate["id"]+"/accept", json={"expectedHeadRevisionId": candidate["baseRevisionId"], "target": candidate["target"]}, headers={"Idempotency-Key": "accept-with-notes"})
    assert accepted.status_code == 200 and accepted.json()['chapterCount'] == 1
    repeated = client.post(BASE+"/candidates/"+candidate["id"]+"/accept", json={"expectedHeadRevisionId": candidate["baseRevisionId"], "target": candidate["target"]}, headers={"Idempotency-Key": "accept-with-notes-again"})
    assert repeated.json() == accepted.json()
    assert "PRIVATE-UNREAD-FUTURE" not in json.dumps(result)
    assert "validationDecision" not in json.dumps(client.get(BASE+"/tasks/"+child.id).json())
