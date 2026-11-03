from app.worker import process

BASE = "/api/reader/v1"


def read_chapter(client, demo, index=0):
    directory = client.get(f"{BASE}/branches/{demo['branchId']}/chapters", params={"revisionId": demo["revisionId"]}).json()
    chapter = client.get(f"{BASE}/branches/{demo['branchId']}/chapters/{directory['items'][index]['id']}", params={"revisionId": demo["revisionId"]})
    assert chapter.status_code == 200, chapter.text
    return chapter.json()


def observe(client, reader, anchor, expected=0, sequence=1):
    result = client.put(f"{BASE}/books/{anchor['bookId']}/progress", json={"branchId": anchor["branchId"], "revisionId": anchor["revisionId"], "anchor": anchor, "expectedProgressVersion": expected, "deviceId": reader["sessionId"], "clientSeq": sequence})
    assert result.status_code == 200, result.text
    return result.json()


def operation(client, profile, anchor, kind="counterfactual", instruction="留下救人", **extra):
    payload = {"operation": kind, "profileId": profile["id"], "anchor": anchor, "expectedHeadRevisionId": anchor["revisionId"], "instruction": instruction, **extra}
    return submit(client, profile, payload)


def submit(client, profile, payload):
    from uuid import uuid4
    plan = client.post(BASE + "/operation-plans", json=payload)
    assert plan.status_code == 201, plan.text
    planned = plan.json()
    consent = client.post(BASE + "/consents", json={"planId": planned["planId"], "planHash": planned["planHash"], "profileId": profile["id"], "maxCalls": planned["maxCalls"], "inputTokens": planned["inputTokens"], "outputTokens": planned["outputTokens"]})
    assert consent.status_code == 201, consent.text
    body = {**payload, "consentId": consent.json()["consentId"]}
    path = BASE + "/generation-tasks"
    if payload["operation"] == "analysis":
        path = f"{BASE}/books/{payload['anchor']['bookId']}/analysis-tasks"
    if payload["operation"] == "role_chat":
        path = f"{BASE}/role-sessions/{payload['sessionId']}/turns"
    if payload["operation"] == "connection_test":
        path = f"{BASE}/model-profiles/{profile['id']}/test"
    result = client.post(path, json=body, headers={"Idempotency-Key": uuid4().hex})
    assert result.status_code == 202, result.text
    process(result.json()["taskId"])
    task = client.get(BASE + "/tasks/" + result.json()["taskId"])
    assert task.status_code == 200, task.text
    return task.json(), body, path


def accept(client, task, target=None, key="accept", policy="require_unchanged_head"):
    candidate = client.get(BASE + "/candidates/" + task["result"]["candidateId"]).json()
    response = client.post(BASE + "/candidates/" + candidate["id"] + "/accept", json={"expectedHeadRevisionId": candidate["baseRevisionId"], "sourcePolicy": policy, "target": target or {"mode": "new_child_branch", "name": "留下救人"}}, headers={"Idempotency-Key": key})
    return response
