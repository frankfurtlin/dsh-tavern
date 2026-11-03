import asyncio
import copy
import io
import json
import time
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Task, UsageRecord, ModelProfile, Candidate, SourceDocument, KnowledgeSnapshot, RoleMessage
from app.providers import MockAdapter, ChatCompletionsAdapter, encrypt_key, validate_url
from app.knowledge import chunks_for, visible_characters
from app.worker import process, generation_context, local_outcome
from app.importer import txt_parse, epub_parse
from app.domain import canonical, utf16_slice
from app.errors import Problem
from .helpers import BASE, read_chapter, observe, operation, submit, accept
from .test_reader import prepare_generation, epub_fixture


def test_F03_shared_frontend_backend_hash_vectors():
    from pathlib import Path
    from app.domain import boundary_hash, digest
    for v in json.loads((Path(__file__).parent/"fixtures/anchor-vectors.json").read_text("utf-8")):
        value = boundary_hash(v["text"],v["revisionId"],v["blockId"],v["start"]) if v["start"]==v["end"] else digest(utf16_slice(v["text"],v["start"],v["end"]))
        assert value == v["hash"]


def test_F17_flashback_alive_does_not_restore_dead_character():
    snapshot=SimpleNamespace(data={"characters":[{"id":"acen","displayName":"阿岑"}],"facts":[
        {"subjectId":"acen","predicate":"alive","value":False,"status":"confirmed","occurredOrder":"death_now","temporalRelation":"at_scope"},
        {"subjectId":"acen","predicate":"alive","value":True,"status":"confirmed","occurredOrder":"two_years_before","temporalRelation":"past"}]})
    result=visible_characters(snapshot)[0]
    assert not result["available"] and result["reasonCode"]=="ROLE_STATE_UNCERTAIN"


def test_F02_selected_encoding_repreviews_before_confirmation(client,reader):
    content="第一章\n甲乙丙，林渡在码头等待。\n第二章\n阿岑抬起头。\n"
    response=client.post(BASE+'/books/imports',files={'file':('encoding.txt',content.encode('gb18030'),'text/plain')},headers={'Idempotency-Key':'encoding-preview-upload'})
    process(response.json()['taskId'])
    task_id=response.json()['taskId']
    preview=client.post(BASE+f'/books/imports/{task_id}/preview',json={'encoding':'gb18030'})
    assert preview.status_code==200,preview.text
    data=preview.json();assert data['sourceFormat']=='txt' and '甲乙丙' in data['preview'] and len(data['chapters'])==2
    created=client.post(BASE+f'/books/imports/{task_id}/confirm',json={'title':'编码确认','encoding':data['encoding'],'parserVersion':'import-v1','chapterOrder':[c['id'] for c in data['chapters']]},headers={'Idempotency-Key':'encoding-preview-confirm'})
    assert created.status_code==201,created.text
    assert '甲乙丙' in read_chapter(client,created.json())['blocks'][0]['text']


def test_orphan_cleanup_keeps_original_normalized_and_recovery_receipts(client,reader,demo):
    import os
    from app.storage import write_private,private_path,clean_orphan_files
    owner=reader['user']['id']
    with SessionLocal() as db:
        source=db.scalar(select(SourceDocument).where(SourceDocument.owner_id==owner))
        retained=[source.file_ref,source.normalized_ref]
    retained.append(write_private(owner,'journal-unsynced-fixture.json',b'{}'))
    orphan=write_private(owner,'unreferenced-upload.tmp',b'temporary')
    for name in [*retained,orphan]:os.utime(private_path(owner,name),(time.time()-172800,)*2)
    clean_orphan_files()
    assert not private_path(owner,orphan).exists()
    assert all(private_path(owner,name).exists() for name in retained)


def test_task_diagnostics_return_durable_phase_and_usage_without_credentials(client,reader,profile):
    task_id=queue_operation(client,profile,{'operation':'connection_test','profileId':profile['id']})
    process(task_id)
    response=client.get(BASE+f'/tasks/{task_id}/diagnostics')
    assert response.status_code==200,response.text
    report=response.json()
    assert report['aiMode']=='mock' and report['profileVersion']==1
    assert all(value>=0 for value in report['phaseMs'].values()) and len(report['usage'])==1
    assert report['usage'][0]['usageState']=='estimated' and 'TEST-KEY' not in response.text
    changed=client.patch(BASE+'/model-profiles/'+profile['id'],json={'expectedVersion':1,'name':'后来更名'})
    assert changed.status_code==200
    frozen=client.get(BASE+f'/tasks/{task_id}/diagnostics').json()
    assert frozen['provider']==profile['name'] and frozen['profileVersion']==1 and frozen['parameters']['calls'][0]['outputLimit']==32


def test_duplicate_import_reuse_closes_confirmation_and_releases_temporary_refs(client,reader,demo):
    from pathlib import Path
    data=(Path(__file__).parents[1]/'app/fixtures/fog-harbor.txt').read_bytes()
    upload=client.post(BASE+'/books/imports',files={'file':('duplicate.txt',data,'text/plain')},headers={'Idempotency-Key':'duplicate-upload'}).json()
    process(upload['taskId'])
    assert client.get(BASE+'/books/imports/'+upload['taskId']).json()['duplicateBookId']==demo['bookId']
    confirmed=client.post(BASE+f"/books/imports/{upload['taskId']}/confirm",json={'title':'复用','parserVersion':'import-v1','reuseDuplicate':True},headers={'Idempotency-Key':'duplicate-confirm'})
    assert confirmed.status_code==201 and confirmed.json()['bookId']==demo['bookId']
    final=client.get(BASE+'/books/imports/'+upload['taskId']).json()
    assert final['importStatus']=='reading_ready' and final['confirmed']['reused']


def create_session(client, demo, task, anchor, name):
    chars = client.get(BASE+f"/books/{demo['bookId']}/characters", params={"branchId": demo["branchId"], "revisionId": demo["revisionId"], "anchor": json.dumps(anchor)}).json()
    character = next(c for c in chars["items"] if c["displayName"] == name)
    result = client.post(BASE+"/role-sessions", json={"bookId": demo["bookId"], "characterId": character["id"], "snapshotId": task["result"]["snapshotId"], "anchor": anchor})
    assert result.status_code == 201, result.text
    return result.json()


def test_F15_character_knowledge_input_isolation_and_pinned_session(client, reader, demo, profile):
    c3 = read_chapter(client, demo, 2)["blocks"][0]["endAnchor"]
    observe(client, reader, c3)
    task, _, _ = operation(client, profile, c3, "analysis")
    before = create_session(client, demo, task, c3, "梅舟")
    captured = []
    validation_frames = []
    original = MockAdapter.generate
    def capture(self, profile, prompt, data, limit):
        if prompt.startswith("role_chat-"):
            captured.append(copy.deepcopy(data))
        if prompt.startswith("validation-"):
            validation_frames.append(copy.deepcopy(data))
        return original(self, profile, prompt, data, limit)
    with patch.object(MockAdapter, "generate", capture):
        first, _, _ = submit(client, profile, {"operation": "role_chat", "profileId": profile["id"], "sessionId": before["id"], "message": "你知道夜渡身份吗？", "turnId": "c3"})
    assert first["result"]["replySaved"], first
    assert captured[0]["prefix"] == []
    assert captured[0]["identityMode"] == validation_frames[0]["identityMode"] == before["identityMode"] == "reader_out_of_story"
    assert validation_frames[0]["operation"] == "role_chat"
    assert "夜渡" not in canonical(captured[0]["characterFacts"]) + canonical(captured[0]["evidence"])
    c4 = read_chapter(client, demo, 3)["blocks"][0]["endAnchor"]
    observe(client, reader, c4, 1, 2)
    task, _, _ = operation(client, profile, c4, "analysis")
    after = create_session(client, demo, task, c4, "梅舟")
    assert before["id"] != after["id"]
    with patch.object(MockAdapter, "generate", capture):
        second, _, _ = submit(client, profile, {"operation": "role_chat", "profileId": profile["id"], "sessionId": after["id"], "message": "你知道夜渡身份吗？", "turnId": "c4"})
    assert second["result"]["replySaved"], second
    assert any(f["predicate"] == "secret_identity" for f in captured[1]["characterFacts"])
    assert "夜渡" in client.get(BASE+f"/role-sessions/{after['id']}/messages").json()["items"][-1]["text"]
    assert client.get(BASE+f"/role-sessions/{before['id']}").json()["pinnedAnchor"] == c3
    assert len(client.get(BASE+f"/role-sessions/{before['id']}/messages").json()["items"]) == 2


def test_F16_two_branches_do_not_share_life_or_chat_memory(client, reader, demo, profile):
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    replies = []
    for index, instruction in enumerate(["留下救人", "驶离东岸"]):
        task, _, _ = operation(client, profile, anchor, instruction=instruction)
        branch = accept(client, task, key=f"branch-{index}").json()
        derived = {**demo, "branchId": branch["branchId"], "revisionId": branch["revisionId"]}
        tail = read_chapter(client, derived, -1)["blocks"][-1]["endAnchor"]
        observe(client, reader, tail, 1, index+2)
        analyzed, _, _ = operation(client, profile, tail, "analysis")
        session = create_session(client, derived, analyzed, tail, "林渡")
        result, _, _ = submit(client, profile, {"operation": "role_chat", "profileId": profile["id"], "sessionId": session["id"], "message": "阿岑现在怎样？", "turnId": f"turn-{index}"})
        assert result["result"]["replySaved"], result
        replies.append(client.get(BASE+f"/role-sessions/{session['id']}/messages").json()["items"][-1]["text"])
    assert "还活着" in replies[0]
    assert "已经不在" in replies[1]


def test_F20_long_story_chunks_budget_identity_continuity_and_generation(client, reader, profile):
    paragraph = "林渡与阿岑沿码头向东走，仍是渡口的两位旧友。" + "潮声轻轻拍着木板。"*40
    content = "第一章 渡口\n" + "\n".join([paragraph]*35) + "\n阿岑死于当夜，林渡独自留在东岸。\n"
    upload = client.post(BASE+"/books/imports", files={"file": ("long.txt", content.encode(), "text/plain")}, headers={"Idempotency-Key": "long-upload"}).json()
    process(upload["taskId"])
    book = client.post(BASE+f"/books/imports/{upload['taskId']}/confirm", json={"title": "长篇回归", "parserVersion": "import-v1"}, headers={"Idempotency-Key": "long-confirm"}).json()
    anchor = read_chapter(client, book)["blocks"][-1]["endAnchor"]
    observe(client, reader, anchor)
    analysis, _, _ = operation(client, profile, anchor, "analysis")
    while analysis['state']=='succeeded' and analysis['result']['aiStatus']=='ai_partial':
        next_plan=client.post(BASE+'/operation-plans',json={'operation':'analysis','profileId':profile['id'],'anchor':anchor,'expectedHeadRevisionId':anchor['revisionId']}).json()
        assert 0<next_plan['batchAnalysisBlocks']<=20
        analysis, _, _ = operation(client, profile, anchor, "analysis")
    assert analysis["state"] == "succeeded" and analysis["result"]["aiStatus"] == "ai_ready", analysis
    with SessionLocal() as db:
        snapshot = db.get(KnowledgeSnapshot, analysis["result"]["snapshotId"])
        people = visible_characters(snapshot)
        assert sum(c["displayName"] == "阿岑" for c in people) == 1, people
        assert next(c for c in people if c["displayName"] == "阿岑")["available"] is False
    generated, _, _ = operation(client, profile, anchor, "continue_at_tail", instruction="驶向外海")
    assert generated["resultOutcome"] == "complete", generated
    with SessionLocal() as db:
        task = db.get(Task, generated["id"])
        data = generation_context(db, task, db.get(KnowledgeSnapshot, task.checkpoint["snapshotId"]))
        assert sum(len(b["text"]) for b in data["prefix"]) < len(content)//2
        assert any(f["predicate"] == "alive" and f["value"] is False for f in data["confirmedFacts"])
        assert all(u.reserved_input <= 12000 for u in db.scalars(select(UsageRecord)))


def test_F22_received_journal_restores_known_usage_exactly_once(client, reader, demo, profile):
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    original = MockAdapter.generate
    def known_result(self, profile, prompt, data, limit):
        result = original(self, profile, prompt, data, limit)
        result["usage"]["state"] = "known"
        return result
    with patch("app.worker.settle_usage", side_effect=OSError("injected DB failure after durable receipt")), patch.object(MockAdapter, "generate", known_result):
        task, _, _ = operation(client, profile, anchor)
    assert task["state"] == "failed" and task["persistenceState"] == "recoverable"
    with SessionLocal() as db:
        record = db.scalar(select(UsageRecord).where(UsageRecord.task_id == task["id"]))
        assert record.status == "intent" and record.usage_state == "unknown"
    def forbid_repeated_analysis(self, profile, prompt, data, limit):
        assert not prompt.startswith("analysis-"), "received analysis must replay locally"
        return known_result(self, profile, prompt, data, limit)
    with patch.object(MockAdapter, "generate", forbid_repeated_analysis):
        process(task["id"])
    with SessionLocal() as db:
        assert db.get(Task, task["id"]).state == "succeeded"
        records = list(db.scalars(select(UsageRecord).where(UsageRecord.task_id == task["id"])))
        assert len(records) == 3 and all(r.status == "completed" and r.usage_state == "known" for r in records)
        reserved = db.get(ModelProfile, profile["id"]).input_reserved
    with patch.object(MockAdapter, "generate", side_effect=AssertionError("no call during replay")):
        process(task["id"])
    with SessionLocal() as db:
        assert db.get(ModelProfile, profile["id"]).input_reserved == reserved
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_F03_durable_source_mapping_crlf_and_emoji(client, reader, encoding):
    text = "第一章\r\n甲😀乙。\r\n相同段落。\r\n相同段落。" if encoding != "gb18030" else "第一章\r\n甲乙。\r\n相同段落。\r\n相同段落。"
    raw = text.encode(encoding)
    response = client.post(BASE+"/books/imports", files={"file": ("source.txt", raw, "text/plain")}, headers={"Idempotency-Key": "source"}).json()
    process(response["taskId"])
    book = client.post(BASE+f"/books/imports/{response['taskId']}/confirm", json={"title": "来源", "encoding": encoding, "parserVersion": "import-v1"}, headers={"Idempotency-Key": "confirm-source"}).json()
    blocks = read_chapter(client, book)["blocks"]
    with SessionLocal() as db:
        source = db.scalar(select(SourceDocument).where(SourceDocument.book_id == book["bookId"]))
        normalized = txt_parse(raw, encoding)["normalized"]
        for block in blocks:
            mapping = source.source_map["blocks"][block["id"]]
            assert utf16_slice(normalized, mapping["normalizedStart"], mapping["normalizedEnd"]) == block["text"]
            codec = "utf-16-le" if encoding == "utf-16" else "utf-8" if encoding == "utf-8-sig" else encoding
            assert raw[mapping["byteStart"]:mapping["byteEnd"]].decode(codec) == block["text"]


def test_F02_epub_drm_and_path_traversal_rejected():
    for name in ["../outside.txt", "META-INF/encryption.xml"]:
        raw = io.BytesIO(epub_fixture())
        with zipfile.ZipFile(raw, "a") as z:
            z.writestr(name, "unsafe")
        with pytest.raises(Problem):
            epub_parse(raw.getvalue(), "0"*32, "unsafe")


def test_F32_real_adapter_contract_uses_pinned_https_no_retry_and_usage(monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "connected"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 2}})
    factory = httpx.AsyncClient
    monkeypatch.setattr("app.providers.validate_url", lambda url: ("approved.example", "93.184.216.34"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    profile = SimpleNamespace(base_url="https://approved.example/v1", key_cipher=encrypt_key("CONTRACT-KEY-NOT-REAL"), model_id="contract-model", output_parameter="max_completion_tokens", supports_json=True)
    result = ChatCompletionsAdapter().generate(profile, "contract", {"_structured": True}, 32)
    assert result["usage"] == {"state": "known", "inputTokens": 7, "outputTokens": 2}
    assert len(calls) == 1 and calls[0].url.host == "93.184.216.34"
    assert calls[0].headers["Host"] == "approved.example"
    assert calls[0].extensions["sni_hostname"] == "approved.example"
    body = json.loads(calls[0].content)
    assert body["max_completion_tokens"] == 32 and "max_tokens" not in body
    assert body["response_format"] == {"type": "json_object"}


def test_F32_F37_provider_total_deadline_unknown_without_retry(monkeypatch):
    factory = httpx.AsyncClient
    requests = []
    async def slow(request):
        requests.append(request)
        await asyncio.sleep(.1)
        return httpx.Response(200, json={})
    real_wait = asyncio.wait_for
    monkeypatch.setattr("app.providers.validate_url", lambda url: ("approved.example", "93.184.216.34"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(slow), **kwargs))
    monkeypatch.setattr(asyncio, "wait_for", lambda awaitable, timeout: real_wait(awaitable, .01))
    profile = SimpleNamespace(base_url="https://approved.example/v1", key_cipher=encrypt_key("CONTRACT-KEY-NOT-REAL"), model_id="contract-model", output_parameter="max_tokens", supports_json=False)
    with pytest.raises(Problem) as error:
        ChatCompletionsAdapter().generate(profile, "contract", {}, 32)
    assert error.value.code == "PROVIDER_STATUS_UNKNOWN" and len(requests) == 1


def test_F23_quality_rejects_partial_repetition_and_input_copy():
    assert local_outcome("未完成"*300, [], "generation", "length") == "partial"
    assert local_outcome("长段落"*200+"\n"+"长段落"*200, [], "generation", "stop") == "too_long"
    assert local_outcome(("重复段落"*35+"\n")*5, [], "generation", "stop") == "repetitive"
    assert local_outcome("原始内容", [{"text": "原始内容"}], "generation", "stop") == "input_copy"


@pytest.mark.parametrize("character_count", [100_000, 1_000_000])
def test_performance_long_import_and_chapter_navigation(client, reader, character_count):
    # Exactly 100 chapters, with legal short paragraphs; no model calls.
    lines = []
    remaining = character_count
    for chapter in range(100):
        heading = f"第{chapter+1}章\n"
        chapter_size = remaining//(100-chapter)
        budget = chapter_size-len(heading)
        paragraphs = []
        while budget:
            take = min(199,budget-1)
            paragraphs.append(("木板沿着河岸向前延伸，风带来潮水的声音。"*12)[:take]+"\n")
            budget -= take+1
        lines.append(heading+"".join(paragraphs))
        remaining -= chapter_size
    content = "".join(lines)
    assert len(content) == character_count
    started = time.monotonic()
    response = client.post(BASE+"/books/imports", files={"file": ("performance.txt", content.encode(), "text/plain")}, headers={"Idempotency-Key": "performance-import"})
    assert response.status_code == 202
    task_id = response.json()["taskId"]
    process(task_id)
    book = client.post(BASE+f"/books/imports/{task_id}/confirm", json={"title": "合成长篇", "parserVersion": "import-v1"}, headers={"Idempotency-Key": "performance-confirm"})
    assert book.status_code == 201, book.text
    elapsed = time.monotonic()-started
    demo = book.json()
    timings=[]
    for _ in range(31):
        begin=time.monotonic()
        directory=client.get(BASE+f"/branches/{demo['branchId']}/chapters").json()
        chapter=client.get(BASE+f"/branches/{demo['branchId']}/chapters/{directory['items'][0]['id']}")
        assert chapter.status_code == 200 and len(directory["items"]) == 100
        timings.append((time.monotonic()-begin)*1000)
    p95=sorted(timings[1:])[28]
    assert elapsed < 60 and p95 < 200, (elapsed,p95)
    assert sum(len(b["text"]) for b in chapter.json()["blocks"]) < character_count/10
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
    from pathlib import Path
    artifact=Path("/artifacts")
    if artifact.exists():
        import resource
        (artifact/f"performance-{character_count}.json").write_text(json.dumps({"targetCharacters":character_count,"actualCharacters":len(content),"chapters":100,"importAndConfirmSeconds":elapsed,"apiDirectoryAndFirstChapterMs": {"cold":timings[0],"hotP95":p95,"hotSamples":30},"pythonMaxRssKiB":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"modelCalls":0,"scope":"actual PostgreSQL + API in-process; excludes browser interactive latency"},indent=2),"utf-8")


def queue_operation(client, profile, payload):
    from uuid import uuid4
    planned=client.post(BASE+"/operation-plans",json=payload).json()
    consent=client.post(BASE+"/consents",json={"planId":planned["planId"],"planHash":planned["planHash"],"profileId":profile["id"],"maxCalls":planned["maxCalls"],"inputTokens":planned["inputTokens"],"outputTokens":planned["outputTokens"]})
    assert consent.status_code==201,consent.text
    path=f"/model-profiles/{profile['id']}/test" if payload["operation"]=="connection_test" else "/generation-tasks"
    response=client.post(BASE+path,json={**payload,"consentId":consent.json()["consentId"]},headers={"Idempotency-Key":uuid4().hex})
    assert response.status_code==202,response.text
    return response.json()["taskId"]


def test_F37_concurrent_tasks_cannot_spend_the_last_call_twice(client, reader, profile):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    with SessionLocal() as db:
        db.get(ModelProfile,profile["id"]).call_budget=1
        db.commit()
    payload={"operation":"connection_test","profileId":profile["id"]}
    ids=[queue_operation(client,profile,payload) for _ in range(2)]
    original=MockAdapter.generate
    calls=[]
    lock=threading.Lock()
    def slow(self,p,prompt,data,limit):
        with lock:calls.append(prompt)
        time.sleep(.05)
        return original(self,p,prompt,data,limit)
    with patch.object(MockAdapter,"generate",slow),ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(process,ids))
    assert len(calls)==1
    statuses=[client.get(BASE+"/tasks/"+id).json() for id in ids]
    assert sorted(t["state"] for t in statuses)==["failed","succeeded"]
    assert next(t for t in statuses if t["state"]=="failed")["errorCode"]=="BUDGET_NOT_AUTHORIZED"
    with SessionLocal() as db:
        assert db.get(ModelProfile,profile["id"]).calls_reserved==1
        assert db.scalar(select(func.count()).select_from(UsageRecord))==1


def test_F19_F23_cancel_during_provider_does_not_save_candidate_or_zero_usage(client, reader, demo, profile):
    anchor=read_chapter(client,demo)["blocks"][1]["endAnchor"]
    observe(client,reader,anchor)
    analyzed,_,_=operation(client,profile,anchor,"analysis")
    assert analyzed["state"]=="succeeded"
    task_id=queue_operation(client,profile,{"operation":"counterfactual","profileId":profile["id"],"anchor":anchor,"expectedHeadRevisionId":anchor["revisionId"],"instruction":"留下救人"})
    original=MockAdapter.generate
    def cancelled(self,p,prompt,data,limit):
        assert client.post(BASE+"/tasks/"+task_id+"/cancel").status_code==200
        result=original(self,p,prompt,data,limit)
        result["usage"]={"state":"unknown","inputTokens":None,"outputTokens":None}
        return result
    with patch.object(MockAdapter,"generate",cancelled):process(task_id)
    task=client.get(BASE+"/tasks/"+task_id).json()
    assert task["state"]=="cancelled" and task["usageState"]=="unknown"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Candidate).where(Candidate.task_id==task_id))==0
        usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task_id))
        assert usage.output_tokens is None and usage.reserved_output==2048


def test_F29_registration_and_login_rate_limits(client):
    for i in range(5):
        assert client.post(BASE+"/auth/register",json={"username":f"limited_{i}","password":"rate-test-password"}).status_code==201
    assert client.post(BASE+"/auth/register",json={"username":"limited_extra","password":"rate-test-password"}).status_code==429
    for _ in range(10):
        assert client.post(BASE+"/auth/login",json={"username":"limited_0","password":"wrong-password-123","clientType":"android"}).status_code==401
    assert client.post(BASE+"/auth/login",json={"username":"limited_0","password":"rate-test-password","clientType":"android"}).status_code==429


def test_F33_cleanup_failure_is_durable_and_retried_without_affecting_other_owner(client, reader, demo, profile):
    from app.models import User, CleanupReceipt, Book
    from app.worker import claim
    from app.security import password_hasher
    from app.storage import write_private, private_path
    from app.api_books import create_book
    from app.domain import digest
    with SessionLocal() as db:
        other=User(username="survivor",password_hash=password_hasher.hash("survivor-password"))
        db.add(other);db.flush()
        owner=other.id
        source=write_private(owner,"survivor.txt","另一位读者的故事。".encode())
        saved=create_book(db,owner,"保留的书",txt_parse("另一位读者的故事。".encode()),digest("survivor"),source,"txt")
        db.commit()
    result=client.post(BASE+"/auth/delete-account",json={"currentPassword":"reader-password-123","confirmation":"删除我的账号"})
    assert result.status_code==202,result.text
    receipt=result.json()["receiptId"]
    task_id=result.json()["taskId"]
    with patch("app.worker.shutil.rmtree",side_effect=OSError("injected filesystem unavailable")):
        process(task_id)
    assert client.get(BASE+f"/auth/deletion-receipts/{receipt}").json()["status"]=="retrying"
    assert client.get(BASE+"/auth/me").status_code==401
    assert claim("cleanup-test-worker")==task_id
    process(task_id)
    assert client.get(BASE+f"/auth/deletion-receipts/{receipt}").json()["status"]=="completed"
    with SessionLocal() as db:
        assert db.get(User,reader["user"]["id"]) is None
        assert db.get(CleanupReceipt,receipt).completed_at is not None
        assert db.get(User,owner).status=="active" and db.get(Book,saved["bookId"]).owner_id==owner
        assert private_path(owner,source).read_text("utf-8")=="另一位读者的故事。"
    with pytest.raises(Problem):
        write_private(reader["user"]["id"],"late-result.json",b"must not recreate deleted account storage")


def test_F37_confirmed_pre_send_failure_releases_budget_once(client, reader, profile, monkeypatch):
    from app.config import settings
    from app.worker import call_provider
    task_id=queue_operation(client,profile,{"operation":"connection_test","profileId":profile["id"]})
    monkeypatch.setattr(settings,"ai_mode","real")
    with patch.object(ChatCompletionsAdapter,"generate",side_effect=Problem("PROVIDER_HOST_UNAVAILABLE","fixture pre-send failure")):
        process(task_id)
    task=client.get(BASE+"/tasks/"+task_id).json()
    assert task["errorCode"]=="PROVIDER_HOST_UNAVAILABLE" and task["providerState"]=="not_sent"
    with SessionLocal() as db:
        stored=db.get(ModelProfile,profile["id"])
        assert (stored.calls_reserved,stored.input_reserved,stored.output_reserved)==(0,0,0)
        usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task_id))
        assert (usage.status,usage.input_tokens,usage.output_tokens)==("not_sent",0,0)
    with patch.object(ChatCompletionsAdapter,"generate",side_effect=AssertionError("must not retry provider")):
        with pytest.raises(Problem):call_provider(task_id,"connection_test","connection",{"message":"连接测试"},32)
    with SessionLocal() as db:
        assert db.get(ModelProfile,profile["id"]).calls_reserved==0


def test_F22_recoverable_role_turn_locks_session_until_journal_saved(client,reader,demo,profile):
    from app.models import RoleSession
    anchor=read_chapter(client,demo)["blocks"][1]["endAnchor"]
    observe(client,reader,anchor)
    analyzed,_,_=operation(client,profile,anchor,"analysis")
    session=create_session(client,demo,analyzed,anchor,"林渡")
    payload={"operation":"role_chat","profileId":profile["id"],"sessionId":session["id"],"message":"此刻想什么？","turnId":"recover-first"}
    planned=client.post(BASE+"/operation-plans",json=payload).json()
    consent=client.post(BASE+"/consents",json={"planId":planned["planId"],"planHash":planned["planHash"],"profileId":profile["id"],"maxCalls":planned["maxCalls"],"inputTokens":planned["inputTokens"],"outputTokens":planned["outputTokens"]}).json()
    response=client.post(BASE+f"/role-sessions/{session['id']}/turns",json={**payload,"consentId":consent["consentId"]},headers={"Idempotency-Key":"recover-first"})
    task_id=response.json()["taskId"]
    with patch("app.worker.settle_usage",side_effect=RuntimeError("injected DB outage after durable receipt")):process(task_id)
    task=client.get(BASE+"/tasks/"+task_id).json()
    assert task["persistenceState"]=="recoverable"
    with SessionLocal() as db:assert db.get(RoleSession,session["id"]).pending_task_id==task_id
    later={**payload,"turnId":"later-turn"}
    plan=client.post(BASE+"/operation-plans",json=later).json()
    second=client.post(BASE+"/consents",json={"planId":plan["planId"],"planHash":plan["planHash"],"profileId":profile["id"],"maxCalls":plan["maxCalls"],"inputTokens":plan["inputTokens"],"outputTokens":plan["outputTokens"]}).json()
    denied=client.post(BASE+f"/role-sessions/{session['id']}/turns",json={**later,"consentId":second["consentId"]},headers={"Idempotency-Key":"later-turn"})
    assert denied.status_code==409 and denied.json()["code"]=="TURN_IN_PROGRESS"
    original=MockAdapter.generate
    def gate_only(self,profile,prompt,data,limit):
        assert prompt.startswith("validation-")
        return original(self,profile,prompt,data,limit)
    with patch.object(MockAdapter,"generate",gate_only):process(task_id)
    assert client.get(BASE+"/tasks/"+task_id).json()["result"]["replySaved"]
    with SessionLocal() as db:assert db.get(RoleSession,session["id"]).pending_task_id is None
    assert len(client.get(BASE+f"/role-sessions/{session['id']}/messages").json()["items"])==2
