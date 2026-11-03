import json
import re
from pathlib import Path
from sqlalchemy import select
from .models import KnowledgeSnapshot, AnalysisChunk, Progress, ContentBlock
from .domain import prefix_blocks, canonical, digest, anchor_order, owned, utf16_len, make_anchor
from .schemas import AnalysisOutput
from .errors import Problem
from .completion_policy import completion_policy, completion_limit, input_ceiling

ANALYZER = "analysis-v17"
ANALYSIS_CHUNK_CHARACTERS = 400


def safe_scope(db, anchor, revision, owner):
    progress = db.scalar(select(Progress).where(Progress.owner_id == owner, Progress.branch_id == anchor["branchId"], Progress.revision_id == revision.id))
    if not progress or anchor_order(revision, anchor) > anchor_order(revision, progress.max_observed_anchor):
        raise Problem("AI_CONTEXT_NOT_READY", "请先阅读到此位置，消息不能解锁未来正文")
    return prefix_blocks(db, revision, anchor, owner)


def chunks_for(blocks, profile=None):
    # Leave room for the extraction contract and evidence-backed identity references.
    window = input_ceiling(profile, completion_limit(profile, "analysis")) if profile else 12000
    prompt_bytes = (Path(__file__).parent / "prompts" / "analysis.txt").stat().st_size
    byte_limit = max(256, window - prompt_bytes - 2000 - 512)
    chunks, chunk, size = [], [], 0
    for block in blocks:
        text = block["text"]
        start = 0
        while start < len(text):
            stop = min(len(text), start + ANALYSIS_CHUNK_CHARACTERS)
            while stop > start + 1 and len(canonical({"blockId": block["blockId"], "text": text[start:stop], "sourceStart": start}).encode()) > byte_limit:
                stop = start + max(1, (stop-start)//2)
            part = {"blockId": block["blockId"], "text": text[start:stop], "sourceStart": start}
            if chunk and (size + len(part["text"]) > ANALYSIS_CHUNK_CHARACTERS or len(canonical(chunk + [part]).encode()) > byte_limit):
                chunks.append(chunk)
                chunk, size = [], 0
            chunk.append(part)
            size += len(part["text"])
            start = stop
    if chunk:
        chunks.append(chunk)
    return chunks


def chunk_hash(chunk, anchor, profile):
    return digest(canonical({"blocks": chunk, "branchId": anchor["branchId"], "analyzer": ANALYZER, "profile": profile.id, "version": profile.version, "analysisPolicy": completion_policy(profile)["stages"]["analysis"]}))


def snapshot_hash(anchor, blocks, profile):
    return digest(canonical({"anchor": anchor, "blocks": blocks, "analyzer": ANALYZER, "profile": profile.id, "version": profile.version, "analysisPolicy": completion_policy(profile)["stages"]["analysis"]}))


def find_snapshot(db, owner, anchor, revision, profile, *, allow_unread=False):
    blocks = prefix_blocks(db, revision, anchor, owner) if allow_unread else safe_scope(db, anchor, revision, owner)
    hashed = snapshot_hash(anchor, blocks, profile)
    snapshot = db.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.owner_id == owner, KnowledgeSnapshot.scope_hash == hashed,
        KnowledgeSnapshot.analyzer_version == ANALYZER, KnowledgeSnapshot.completeness == "complete"))
    return blocks, hashed, snapshot


def missing_chunks(db, owner, anchor, blocks, profile):
    chunks = chunks_for(blocks, profile)
    missing = []
    for chunk in chunks:
        hashed = chunk_hash(chunk, anchor, profile)
        cached = db.scalar(select(AnalysisChunk).where(AnalysisChunk.owner_id == owner, AnalysisChunk.cache_hash == hashed))
        if not cached:
            missing.append((hashed, chunk))
    return chunks, missing


def materialize_cached_snapshot(db, owner, anchor, revision, profile):
    """Bind already verified chunks to this exact anchor without a provider call."""
    blocks, scope_hash, snapshot = find_snapshot(db, owner, anchor, revision, profile)
    if snapshot:
        return snapshot
    chunks, missing = missing_chunks(db, owner, anchor, blocks, profile)
    if missing:
        return None
    parts = [db.scalar(select(AnalysisChunk).where(AnalysisChunk.owner_id == owner, AnalysisChunk.cache_hash == chunk_hash(chunk, anchor, profile))).data for chunk in chunks]
    snapshot = KnowledgeSnapshot(owner_id=owner, book_id=anchor['bookId'], branch_id=anchor['branchId'], revision_id=revision.id, boundary=anchor,
        scope_hash=scope_hash, analyzer_version=ANALYZER, profile_version=f'{profile.id}:{profile.version}', data=merge_analysis(parts), completeness='complete')
    db.add(snapshot)
    db.flush()
    return snapshot


def parse_json_local(text):
    # One local repair only: strip code fence or isolate the one complete JSON object.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        trimmed = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        start, end = trimmed.find("{"), trimmed.rfind("}")
        if start < 0 or end < start:
            raise Problem("ANALYSIS_INVALID", "分析输出结构无效")
        try:
            return json.loads(trimmed[start:end + 1])
        except json.JSONDecodeError:
            raise Problem("ANALYSIS_INVALID", "分析输出结构无效")


def validate_analysis(text, chunk, scope_hash, db, owner, anchor, prior_characters=None):
    try:
        data = AnalysisOutput.model_validate(parse_json_local(text)).model_dump()
    except Problem:
        raise
    except Exception:
        raise Problem("ANALYSIS_INVALID", "分析输出不符合证据契约")
    if data["scopeHash"] != scope_hash:
        raise Problem("ANALYSIS_SCOPE_INVALID", "分析范围校验失败")
    evidence = {}
    for e in data["evidence"]:
        if e["id"] in evidence:
            raise Problem("ANALYSIS_INVALID", "分析证据标识重复")
        matches = [b for b in chunk if b["blockId"] == e["blockId"] and b["text"].count(e["quote"]) == 1]
        block = matches[0] if len(matches) == 1 else None
        if not block:
            # Ambiguous evidence remains uncertain and cannot establish character knowledge.
            continue
        start = block.get("sourceStart", 0) + block["text"].index(e["quote"])
        source = owned(db, ContentBlock, e["blockId"], owner)
        start16 = utf16_len(source.text[:start])
        evidence[e["id"]] = {**e, "id": digest(scope_hash + e["id"])[:32],
            "anchor": make_anchor(anchor["bookId"], anchor["branchId"], anchor["revisionId"], source, start16, start16 + utf16_len(e["quote"])), "quoteHash": digest(e["quote"])}
    if len({c['localId'] for c in data['characters']}) != len(data['characters']) or len({f['localId'] for f in data['facts']}) != len(data['facts']):
        raise Problem('ANALYSIS_INVALID', '分析角色或事实标识重复')
    local_chars = {}
    identity_claims = {}
    chars = []
    for c in data["characters"]:
        valid = [evidence[e] for e in c["evidenceIds"] if e in evidence]
        if not valid or not any(c["displayName"] in e["quote"] for e in valid):
            continue
        # Stable scope entity ID comes from an exact first occurrence, never a global name key.
        first = valid[0]['anchor']
        cid = digest(canonical({'branchId': anchor['branchId'], 'displayName': c['displayName'],
            'firstOccurrence': {key: first[key] for key in ['blockId', 'start', 'end']}}))[:32]
        prior = next((p for p in (prior_characters or []) if p["id"] == c["priorRef"]), None)
        if c["priorRef"] and (not prior or prior["displayName"] != c["displayName"]):
            raise Problem("ANALYSIS_IDENTITY_INVALID", "角色继承缺少范围内的证据")
        inherited = bool(prior and c['continuity'] == 'explicit')
        if inherited:
            cid = prior["id"]
        if cid in identity_claims and (not inherited or not identity_claims[cid]):
            raise Problem('ANALYSIS_IDENTITY_INVALID', '同一首次证据不能建立多个未确认身份')
        identity_claims[cid] = inherited
        local_chars[c["localId"]] = cid
        chars.append({"id": cid, "displayName": c["displayName"], "evidenceIds": [e["id"] for e in valid], 'identityProofVersion': 2, 'identityInherited': inherited})
    facts = []
    for fact in data["facts"]:
        valid = [evidence[e] for e in fact["evidenceIds"] if e in evidence]
        if not valid or fact["subjectRef"] not in local_chars:
            continue
        knowledge = []
        for claim in fact["knowledge"]:
            knower = claim["characterRef"]
            learned_evidence = [evidence[e] for e in claim["evidenceIds"] if e in evidence]
            if knower not in fact["knownBy"] or knower not in local_chars or not learned_evidence or fact["certainty"] != "explicit" or claim["validity"] != "at_scope":
                continue
            knowledge.append({"characterId": local_chars[knower], "evidenceIds": [e["id"] for e in learned_evidence], "learnedAt": learned_evidence[-1]["anchor"], "learnedOrder": claim["learnedOrder"], "validity": claim["validity"]})
        facts.append({**fact, "knowledge": knowledge, "id": digest(scope_hash + fact["localId"])[:32], "subjectId": local_chars[fact["subjectRef"]],
                      "evidenceIds": [e["id"] for e in valid], "status": "confirmed" if fact["certainty"] == "explicit" else "uncertain",
                      "readerRevealAnchor": max(valid, key=lambda e: e["anchor"]["end"])["anchor"],
                      "knownBy": [claim["characterId"] for claim in knowledge], "validFrom": fact["occurredOrder"], "validUntil": None})
    # Model free-form summaries are not treated as verified evidence. Rebuild from exact quotes.
    return {"characters": chars, "facts": facts, "evidence": list(evidence.values()), "summary": "\n".join(e["quote"] for e in evidence.values()), "unresolved": data["unresolved"]}


def merge_analysis(parts):
    chars, evidence, facts = {}, {}, []
    for part in parts:
        counts = {}
        for c in part['characters']:
            counts[c['id']] = counts.get(c['id'], 0) + 1
        for c in part["characters"]:
            previous = chars.get(c["id"])
            unclear = counts[c['id']] > 1 and not all(other.get('identityProofVersion') == 2 and other.get('identityInherited') for other in part['characters'] if other['id'] == c['id'])
            chars[c["id"]] = {**c, "evidenceIds": list(dict.fromkeys((previous or {}).get("evidenceIds", []) + c["evidenceIds"])), 'identityUncertain': bool(unclear or c.get('identityUncertain') or (previous or {}).get('identityUncertain'))}
        for e in part["evidence"]:
            evidence[e["id"]] = e
        facts.extend(part["facts"])
    proof = all(c.get('identityProofVersion') == 2 for part in parts for c in part['characters'])
    return {"characters": list(chars.values()), "facts": facts, "evidence": list(evidence.values()), 'identityProofVersion': 2 if proof else 0,
            "summary": "\n".join(e["quote"] for e in evidence.values()), "unresolved": sum([p["unresolved"] for p in parts], [])}


def prior_identity_context(merged, joined, revision):
    """Provide exact first/recent identity evidence without trimming its text."""
    evidence = {e['id']: e for e in merged['evidence']}
    characters, quotes = [], {}
    for character in merged['characters']:
        if character['displayName'] not in joined:
            continue
        if not character['evidenceIds'] or any(eid not in evidence for eid in character['evidenceIds']):
            raise Problem('ANALYSIS_IDENTITY_INVALID', '角色承接缺少已验证证据')
        records = sorted((evidence[eid] for eid in character['evidenceIds']),
            key=lambda e: anchor_order(revision, e['anchor'])[:2] + (e['anchor']['start'], e['anchor']['end']))
        selected = list({e['id']: e for e in [records[0], records[-1]]}.values())
        characters.append({'id': character['id'], 'displayName': character['displayName'],
            'evidenceIds': [e['id'] for e in selected],
            'identityUncertain': bool(character.get('identityUncertain')) or sum(c['displayName'] == character['displayName'] for c in merged['characters']) > 1})
        for e in selected:
            group = quotes.setdefault(e['quote'], {'evidenceIds': [], 'quote': e['quote']})
            if e['id'] not in group['evidenceIds']:
                group['evidenceIds'].append(e['id'])
    context = {'priorCharacters': characters, 'priorIdentityEvidence': list(quotes.values())}
    if len(canonical(context).encode()) > 2000:
        raise Problem('CONTEXT_BUDGET_EXCEEDED', '角色承接证据超过预算，未省略身份约束')
    return context


def visible_characters(snapshot):
    chars = []
    for character in snapshot.data["characters"]:
        subject_facts = [f for f in snapshot.data["facts"] if f["subjectId"] == character["id"]]
        facts = [f for f in subject_facts if f["status"] == "confirmed"]
        relevant_states = [f for f in subject_facts if f["predicate"] == "alive" and f.get("temporalRelation") != "future"]
        states = [f for f in relevant_states if f["status"] == "confirmed" and type(f["value"]) is bool and f.get("temporalRelation") in {"at_scope", "past"}]
        alive = states[-1]["value"] if states else True
        unreliable_state = len(states) != len(relevant_states)
        flashback_conflict = any(f["value"] is False for f in states) and alive is True
        uncertain_state = unreliable_state or flashback_conflict
        if flashback_conflict and not unreliable_state:
            # A later textual flashback does not undo a death. Proving a resurrection
            # needs an explicit current event, not the ordering of extraction results.
            revived = any(f["predicate"] in {"revived", "resurrected"} and f["value"] is True and f.get("temporalRelation") == "at_scope" for f in facts)
            uncertain_state = not revived
        ambiguous = bool(character.get('identityUncertain')) or (not character.get('readerIdentityConfirmed') and
            sum(c["displayName"] == character["displayName"] for c in snapshot.data["characters"]) > 1)
        chars.append({**character, "available": alive is not False and not ambiguous and not uncertain_state, "reasonCode": "ROLE_IDENTITY_UNCERTAIN" if ambiguous else "ROLE_STATE_UNCERTAIN" if uncertain_state else "ROLE_DECEASED" if alive is False else None})
    return chars


def legacy_identity_uncertain(db, snapshot, character):
    if snapshot.data.get('readerCorrectionApplied') and character.get('readerIdentityConfirmed'):
        return False  # Explicit, evidence-validated exact-scope choice; no UUID merge.
    if snapshot.data.get('identityProofVersion') == 2:
        return False
    selected = set(character.get('evidenceIds', []))
    if not selected:
        return True
    chunks = db.scalars(select(AnalysisChunk).where(AnalysisChunk.owner_id == snapshot.owner_id,
        AnalysisChunk.branch_id == snapshot.branch_id,
        AnalysisChunk.data['characters'].contains([{'id': character['id']}]))).all()
    origins, matched = {}, False
    for chunk in chunks:
        claims = [c for c in chunk.data['characters'] if c['id'] == character['id'] and selected.intersection(c['evidenceIds'])]
        if not claims:
            continue
        matched = True
        if len(claims) > 1:
            return True
        for evidence in chunk.data['evidence']:
            if evidence['id'] not in selected:
                continue
            block_id = evidence['anchor']['blockId']
            if digest(snapshot.branch_id + character['displayName'] + block_id)[:32] == character['id']:
                origins.setdefault(block_id, set()).add(chunk.cache_hash)
    # Old data discarded inheritance provenance inside one long physical block.
    # Never consult evidence from after this fixed snapshot.
    return not matched or any(len(hashes) > 1 for hashes in origins.values())


def require_role_character(snapshot, character_id, db=None):
    character = next((c for c in visible_characters(snapshot) if c["id"] == character_id), None)
    if not character or not character["available"] or (db is not None and legacy_identity_uncertain(db, snapshot, character)):
        raise Problem("ROLE_UNAVAILABLE", "此角色在该时间点不可对话，请选择已登场且生死状态明确的时间点")
    return character


def character_voice(snapshot, character_id):
    """Voice traits carry no free-form narration or character world knowledge."""
    styles = {'简短': 'concise', '简洁': 'concise', '行动导向': 'practical', '具体行动': 'practical', '温和': 'gentle', '正式': 'formal', '口语': 'colloquial', '幽默': 'humorous', '谨慎': 'careful'}
    traits, evidence = [], []
    for fact in snapshot.data['facts']:
        predicate = fact['predicate'].replace('_', '').lower()
        if fact['subjectId'] != character_id or fact['status'] != 'confirmed' or fact.get('temporalRelation') != 'at_scope' or predicate not in {'speakingstyle', 'speechstyle', 'voicestyle'}:
            continue
        value = str(fact['value'])
        found = []
        for phrase, label in styles.items():
            for match in re.finditer(re.escape(phrase), value):
                preceding = re.split(r'[，。；！？,;.!?]', value[max(0, match.start() - 8):match.start()])[-1]
                if not re.search(r'不|非|没有|从未|缺乏|很少|鲜少|无', preceding):
                    found.append(label)
                    break
        if found:
            traits.extend(found)
            evidence.extend(fact['evidenceIds'])
    return {'traits': list(dict.fromkeys(traits)), 'evidenceIds': list(dict.fromkeys(evidence))}
