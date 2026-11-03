"""Stable scoped UUID foundation; deterministic DB/provenance, no paid providers."""
import json
from copy import deepcopy
from uuid import UUID
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func, text
from alembic import command
from alembic.config import Config
from app.config import settings
from app.db import SessionLocal, engine
from app.models import (CharacterIdentity, CharacterAlias, CharacterScopeRevision, KnowledgeSnapshot, RoleSession,
    ContentBlock, Book, Revision, SourceDocument, UsageRecord, Progress, uid)
from app.character_identity import reader_identity, reader_fields, resolve_reader_character
from app.world.models import World, WorldCheckpoint
from app.world.preparation import merge
from .test_world_security import isolated_storage_and_mock_only
from .test_character_cards import index, card_list
from .test_world import start, post, API
from .helpers import BASE, read_chapter, observe


def indexed(client, reader, demo, profile):
    index(client, demo)
    at = read_chapter(client, demo)["blocks"][-1]["endAnchor"]
    observe(client, reader, at)
    cards = card_list(client, demo, at).json()["items"]
    return at, cards


def test_reader_cards_chat_uuid_same_reference_and_legacy_id_unchanged(client, reader, demo, profile):
    at, cards = indexed(client, reader, demo, profile)
    selected = next(c for c in cards if c["name"] == "林渡")
    assert UUID(hex=selected["identityId"]).version == 5
    assert selected["identityId"] != selected["id"]
    response = client.post(BASE + "/books/" + demo["bookId"] + "/character-chats", json={"anchor": at,
        "characterId": selected["identityId"]}, headers={"Idempotency-Key": "uuid-direct-chat"})
    assert response.status_code == 201, response.text
    assert response.json()["characterId"] == selected["id"]
    assert response.json()["characterIdentityId"] == selected["identityId"]
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(UsageRecord))
        aliases = db.scalar(select(func.count()).select_from(CharacterAlias))
    again = card_list(client, demo, at).json()["items"]
    assert next(c for c in again if c["id"] == selected["id"])["identityId"] == selected["identityId"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before
        assert db.scalar(select(func.count()).select_from(CharacterAlias)) == aliases


def test_same_name_distinct_proofs_books_and_explicit_scoped_aliases(client, reader, demo):
    owner = reader["user"]["id"]
    with SessionLocal() as db:
        block = db.scalar(select(ContentBlock).where(ContentBlock.book_id == demo["bookId"]))
        def snapshot(branch, characters, evidence):
            return SimpleNamespace(owner_id=owner, book_id=demo["bookId"], branch_id=branch,
                revision_id=demo["revisionId"], boundary={}, scope_hash=uid(),
                data={"characters": characters, "evidence": evidence, "identityProofVersion": 2})
        proof = lambda key, start, quote: {"id": key, "quote": quote, "anchor": {"blockId": block.id, "start": start, "end": start + len(quote)}}
        a = {"id": "first", "displayName": "阿岑", "evidenceIds": ["a"], "identityProofVersion": 2}
        b = {"id": "second", "displayName": "阿岑", "evidenceIds": ["b"], "identityProofVersion": 2}
        s = snapshot(demo["branchId"], [a,b], [proof("a",0,"阿岑在码头"), proof("b",40,"另一位阿岑到来")])
        ia, ib = reader_identity(db,s,a), reader_identity(db,s,b)
        assert ia.id != ib.id
        alias = {**a, "displayName": "岑先生"}
        assert reader_identity(db,s,alias).id == ia.id  # Same explicit old entity, not a name guess.
        other = snapshot(uid(), [{**a,"id":"other"}], [{**proof("a",0,"阿岑在码头"),"anchor":{"blockId":uid(),"start":0,"end":5}}])
        assert reader_identity(db,other,other.data["characters"][0]).id != ia.id
        copied = snapshot(uid(), [{**a,"id":"copied"}], s.data["evidence"])
        assert reader_identity(db,copied,copied.data["characters"][0]).id == ia.id  # Shared exact block proof.
        db.commit()


@pytest.mark.parametrize("same_paragraph", [False, True])
def test_preparation_does_not_merge_two_people_only_by_equal_name_or_alias(same_paragraph):
    from .test_world import definition
    before = definition()
    next(e for e in before["entities"] if e["id"] == "npc")["evidence"] = [{"blockId":"shared-paragraph", "quote":"两位梅舟在渡口相遇"}]
    incoming = deepcopy(before)
    original = next(e for e in incoming["entities"] if e["id"] == "npc")
    original.update(id="different-person", name="梅舟", evidence=deepcopy(original["evidence"]) if same_paragraph else [{"blockId":"other-block", "quote":"另一位梅舟走来"}])
    incoming["initial"]["different-person"] = incoming["initial"].pop("npc", {})
    for entry in incoming["lore"]:
        entry["knownBy"] = ["different-person" if x == "npc" else x for x in entry["knownBy"]]
        entry["entityIds"] = ["different-person" if x == "npc" else x for x in entry["entityIds"]]
    result = merge(before, incoming)
    assert {e["id"] for e in result["entities"] if e["name"] == "梅舟"} == {"npc", "different-person"}


def test_new_preparation_character_keeps_empty_required_fields():
    from .test_world import definition
    old=definition();incoming=deepcopy(old)
    character=deepcopy(next(e for e in incoming['entities'] if e['type']=='character'))
    character.update(id='new-person',name='新的旅人',traits=[])
    incoming['entities'].append(character)
    incoming['initial']['new-person']=deepcopy(next(iter(incoming['initial'].values())))
    result=merge(old,incoming)
    assert next(e for e in result['entities'] if e['id']=='new-person')['traits']==[]


def test_source_world_publication_and_source_delete_keep_uuid_without_changing_history(client, reader, demo, profile):
    at, cards = indexed(client, reader, demo, profile)
    selected = next(c for c in cards if c["name"] == "林渡")
    world, line = start(client, reader, demo, profile)
    actor = next(c for c in world["characters"] if c["name"] == "林渡")
    assert actor["identityId"] == selected["identityId"]
    entity = next(e for e in line["head"]["characterScopes"] if e["entityId"] == actor["id"])
    assert entity["identityId"] == selected["identityId"]
    with SessionLocal() as db:
        old = db.get(WorldCheckpoint,line["headCheckpointId"])
        old_data = deepcopy((old.definition,old.state,old.content_hash))
        before = db.scalar(select(func.count()).select_from(UsageRecord))
    published = post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},"identity-publication")
    with SessionLocal() as db:
        source=db.get(SourceDocument,db.get(Book,published["bookId"]).source_id)
        refs=source.source_map["characterBindings"]
        assert any(r["identityId"]==selected["identityId"] for r in refs)
        assert "hiddenGoal" not in json.dumps(refs)
    deleted=client.request("DELETE",BASE+"/books/"+demo["bookId"],json={"expectedVersion":1},headers={"Idempotency-Key":"identity-delete-source"})
    assert deleted.status_code==202,deleted.text
    after=client.get(API+f"/worlds/{world['id']}/lines/{line['id']}").json()
    assert next(e for e in after["head"]["characterScopes"] if e["entityId"]==actor["id"])["identityId"]==selected["identityId"]
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,line["headCheckpointId"])
        assert (cp.definition,cp.state,cp.content_hash)==old_data
        assert db.scalar(select(func.count()).select_from(UsageRecord))==before
        assert db.get(CharacterIdentity,selected["identityId"]) is not None
        assert not db.scalar(select(func.count()).select_from(CharacterScopeRevision).where(CharacterScopeRevision.context["bookId"].astext==demo["bookId"]))


def test_identity_scope_facts_and_unknown_uuid_are_not_global_authority(client,reader,demo,profile):
    at,cards=indexed(client,reader,demo,profile)
    selected=next(c for c in cards if c["name"]=="阿岑")
    later=read_chapter(client,demo,1)["blocks"][0]["endAnchor"];observe(client,reader,later,expected=1,sequence=2)
    dead=next(c for c in card_list(client,demo,later).json()["items"] if c["name"]=="阿岑")
    assert dead["identityId"]==selected["identityId"] and not dead["available"]
    assert selected["scopeRevisionId"]!=dead["scopeRevisionId"]
    refused=client.post(BASE+"/books/"+demo["bookId"]+"/character-chats",json={"anchor":later,"characterId":selected["identityId"]},headers={"Idempotency-Key":"dead-uuid"})
    assert refused.status_code==422 and refused.json()["code"]=="ROLE_UNAVAILABLE"
    wrong=client.post(BASE+"/books/"+demo["bookId"]+"/character-chats",json={"anchor":at,"characterId":uid()},headers={"Idempotency-Key":"wrong-uuid"})
    assert wrong.status_code==422
    earlier=next(c for c in card_list(client,demo,at).json()["items"] if c["name"]=="阿岑")
    assert earlier["available"] and "离世" not in earlier["description"]
    with SessionLocal() as db:
        old=db.get(CharacterScopeRevision,selected["scopeRevisionId"])
        assert old.visible_data["available"] and "离世" not in json.dumps(old.visible_data,ensure_ascii=False)


def test_0007_real_upgrade_downgrade_preserves_ids_hashes_and_backfills_old_sessions(client,reader,demo,profile):
    at,cards=indexed(client,reader,demo,profile)
    chosen=next(c for c in cards if c["name"]=="林渡")
    role=client.post(BASE+"/books/"+demo["bookId"]+"/character-chats",json={"anchor":at,"characterId":chosen["id"]},headers={"Idempotency-Key":"migration-old-role"}).json()
    world, line = start(client,reader,demo,profile)
    actor = next(c for c in world["characters"] if c["name"]=="林渡")
    assert actor["identityId"]==chosen["identityId"]
    with SessionLocal() as db:
        old=[(b.id,b.text,b.content_hash) for b in db.scalars(select(ContentBlock).order_by(ContentBlock.id))]
        session=db.get(RoleSession,role["id"]);legacy=(session.character_id,session.pinned_anchor)
        original_cp = db.get(WorldCheckpoint,line["headCheckpointId"])
        old_checkpoint = deepcopy((original_cp.definition,original_cp.state,original_cp.content_hash))
    cfg=Config("alembic.ini");command.downgrade(cfg,"0006")
    try:
        assert client.get("/health/ready").status_code==503
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='character_identities'"))==0
        command.upgrade(cfg,"head")
        assert client.get("/health/ready").status_code==200
        with SessionLocal() as db:
            assert [(b.id,b.text,b.content_hash) for b in db.scalars(select(ContentBlock).order_by(ContentBlock.id))]==old
            session=db.get(RoleSession,role["id"])
            assert (session.character_id,session.pinned_anchor)==legacy
            assert session.character_identity_id==chosen["identityId"]
            assert db.get(CharacterIdentity,session.character_identity_id)
            cp=db.get(WorldCheckpoint,line["headCheckpointId"])
            assert (cp.definition,cp.state,cp.content_hash)==old_checkpoint
        restored=client.get(API+"/worlds/"+world["id"]).json()
        assert next(c for c in restored["characters"] if c["id"]==actor["id"])["identityId"]==chosen["identityId"]
    finally:command.upgrade(cfg,"head")


def test_world_begin_and_identity_fork_accept_uuid_but_keep_local_ids_and_private_scope(client, reader, profile):
    from .test_world_standalone import create
    from app.world.worker import process
    made = create(client)
    world = client.get(API + '/worlds/' + made['worldId']).json()
    actor, other = world['characters'][:2]
    job = post(client, '/worlds/' + world['id'] + '/begin', {'characterId':actor['identityId']}, 'begin-uuid')
    process(job['taskId'])
    line = client.get(API + f"/worlds/{world['id']}/lines/{job['lineId']}").json()
    assert line['head']['state']['playerId'] == actor['id']
    frozen = deepcopy(line['head']['state'])
    switched = post(client, '/worlds/' + world['id'] + '/fork', {'checkpointId':line['headCheckpointId'],
        'mode':'identity', 'characterId':other['identityId']}, 'fork-uuid')
    assert switched['head']['state']['playerId'] == other['id']
    old = client.get(API + f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}").json()
    assert old['state'] == frozen
    old_scopes = {e['entityId']:e for e in old['characterScopes']}
    new_scopes = {e['entityId']:e for e in switched['head']['characterScopes']}
    assert new_scopes[other['id']]['identityId'] == other['identityId']
    assert old_scopes[actor['id']]['scopeRevisionId'] != new_scopes[actor['id']]['scopeRevisionId']
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint,line['headCheckpointId'])
        secrets = [l['text'] for l in cp.definition['lore'] if l['id'] not in cp.state['known']]
        secrets += [e.get('hiddenGoal','') for e in cp.definition['entities'] if e['id'] != actor['id']]
        old_data = json.dumps(db.get(CharacterScopeRevision,old_scopes[actor['id']]['scopeRevisionId']).visible_data,ensure_ascii=False)
        assert all(secret not in old_data and secret not in json.dumps(old,ensure_ascii=False) for secret in secrets if len(secret)>=4)


def test_uuid_is_not_authorization_to_another_owner_or_another_world(client, reader, demo, profile):
    from .test_world_standalone import create
    from .test_world_security import _login_other
    _, cards = indexed(client,reader,demo,profile)
    identity = cards[0]['identityId']
    first=create(client); second=create(client,key='different-world')
    actor=client.get(API+'/worlds/'+first['worldId']).json()['characters'][0]
    before_ids=[]
    with SessionLocal() as db:
        before_ids=list(db.scalars(select(CharacterAlias.id)))
        usage_before=db.scalar(select(func.count()).select_from(UsageRecord))
    bad=client.post(API+'/worlds/'+second['worldId']+'/begin',json={'characterId':actor['identityId']},headers={'Idempotency-Key':'foreign-world-uuid'})
    assert bad.status_code==422 and bad.json()['code']=='WORLD_CHARACTER_INVALID'
    other=_login_other(client)
    rejected=client.post(BASE+'/books/'+demo['bookId']+'/character-chats',json={'anchor':demo['readingAnchor'],'characterId':identity},headers={'Authorization':'Bearer '+other['accessToken'],'Idempotency-Key':'foreign-owner-uuid'})
    assert rejected.status_code==404
    with SessionLocal() as db:
        assert list(db.scalars(select(CharacterAlias.id)))==before_ids
        assert db.scalar(select(func.count()).select_from(UsageRecord))==usage_before


def test_published_visible_proof_reanalysis_uses_same_uuid_not_name_merge(client, reader, demo, profile):
    at, cards=indexed(client,reader,demo,profile)
    chosen=next(c for c in cards if c['name']=='林渡')
    world,line=start(client,reader,demo,profile)
    saved=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},'published-index')
    published={'bookId':saved['bookId'],'branchId':saved['branchId'],'revisionId':saved['revisionId']}
    index(client,published,key='published-index-new')
    position=read_chapter(client,published)['blocks'][-1]['endAnchor']
    with SessionLocal() as db:
        progress = db.scalar(select(Progress).where(Progress.branch_id==published['branchId']))
        version = progress.version if progress else 0
    observe(client,reader,position,expected=version,sequence=2)
    again=next(c for c in card_list(client,published,position).json()['items'] if c['name']=='林渡')
    assert again['identityId']==chosen['identityId']
    session=client.post(BASE+'/books/'+saved['bookId']+'/character-chats',json={'anchor':position,'characterId':chosen['identityId']},headers={'Idempotency-Key':'published-uuid-chat'})
    assert session.status_code==201 and session.json()['characterIdentityId']==chosen['identityId']


def test_explicit_source_reference_requires_owned_frozen_exact_evidence(client, reader, demo, profile):
    from app.character_identity import world_reference
    from app.errors import Problem
    _,cards=indexed(client,reader,demo,profile)
    world,line=start(client,reader,demo,profile)
    with SessionLocal() as db:
        stored=db.get(World,world['id'])
        actor=deepcopy(next(e for e in stored.definition['entities'] if e['name']=='林渡'))
        identity=next(c for c in cards if c['name']=='林渡')['identityId']
        actor['sourceCharacterId']=identity
        assert world_reference(stored,actor)==identity
        actor['sourceCharacterId']=uid()
        with pytest.raises(Problem) as absent:world_reference(stored,actor)
        assert absent.value.code=='CHARACTER_REFERENCE_INVALID'
        actor['sourceCharacterId']=identity
        actor['evidence']=[{'blockId':stored.source['blocks'][0]['blockId'],'quote':'林渡秘密冒充另一个人'}]
        with pytest.raises(Problem):world_reference(stored,actor)
        actor['evidence']=[]
        with pytest.raises(Problem):world_reference(stored,actor)


def test_explicit_source_uuid_accepts_its_later_exact_evidence_only(client,reader,demo):
    from app.character_identity import world_reference,bind
    from app.errors import Problem
    owner=reader['user']['id'];first_id,later_id=uid(),uid()
    with SessionLocal() as db:
        known=bind(db,owner,'reader',demo['branchId'],'known-person')
        proof={'blockId':first_id,'start':0,'originalLabel':'林渡'}
        later={'blockId':later_id,'start':2,'originalLabel':'林渡'}
        actor={'id':'world-person','name':'林渡','type':'character','source':'original','aliases':[],
            'sourceCharacterId':known.id,'evidence':[{'blockId':later_id,'quote':'😀林渡又登上渡船。'}]}
        frozen=SimpleNamespace(owner_id=owner,id=uid(),definition={'entities':[actor]},source={
            'blocks':[{'blockId':first_id,'text':'林渡来到渡口。'},{'blockId':later_id,'text':'😀林渡又登上渡船。'}],
            'characterRefs':[{'identityId':known.id,'entityId':'known-person','name':'林渡','proof':proof,'proofs':[proof,later],
                'evidence':[{'blockId':first_id,'quote':'林渡来到渡口。'},{'blockId':later_id,'quote':'😀林渡又登上渡船。'}]}]})
        assert world_reference(frozen,actor)==known.id
        actor['evidence']=[{'blockId':later_id,'quote':'另一个林渡登上渡船。'}]
        with pytest.raises(Problem) as wrong:world_reference(frozen,actor)
        assert wrong.value.code=='CHARACTER_REFERENCE_INVALID'
        actor['evidence']=[]
        with pytest.raises(Problem):world_reference(frozen,actor)


def test_account_purge_cascades_identity_aliases_scopes_and_new_role_column(client,reader,demo,profile):
    from app.worker import process
    from app.models import User
    at,cards=indexed(client,reader,demo,profile)
    role=client.post(BASE+'/books/'+demo['bookId']+'/character-chats',json={'anchor':at,'characterId':cards[0]['identityId']},headers={'Idempotency-Key':'purge-identity'}).json()
    with SessionLocal() as db:
        assert db.get(RoleSession,role['id']).character_identity_id
        assert all(db.scalar(select(func.count()).select_from(model))>0 for model in (CharacterIdentity,CharacterAlias,CharacterScopeRevision))
    response=client.post(BASE+'/auth/delete-account',json={'currentPassword':'reader-password-123','confirmation':'删除我的账号'})
    assert response.status_code==202,response.text
    process(response.json()['taskId'])
    with SessionLocal() as db:
        for model in (CharacterIdentity,CharacterAlias,CharacterScopeRevision,RoleSession):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==reader['user']['id']))==0


def test_same_name_same_paragraph_ambiguous_legacy_ids_are_not_merged(client,reader,demo):
    with SessionLocal() as db:
        proof={"id":"e", "quote":"两位阿岑在这里相遇", "anchor":{"blockId":uid(),"start":0,"end":10}}
        a={"id":"first", "displayName":"阿岑", "evidenceIds":["e"], "identityProofVersion":2}
        b={**a,"id":"second"}
        snapshot=SimpleNamespace(owner_id=reader["user"]["id"],book_id=demo["bookId"],branch_id=demo["branchId"],
            data={"characters":[a,b],"evidence":[proof],"identityProofVersion":2})
        assert reader_identity(db,snapshot,a).id != reader_identity(db,snapshot,b).id


def test_concurrent_first_cards_have_one_uuid_alias_and_scope_revision(client,reader,demo,profile):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from app.main import app
    index(client,demo)
    at=read_chapter(client,demo)['blocks'][0]['endAnchor'];observe(client,reader,at)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CharacterIdentity))==0
        before=db.scalar(select(func.count()).select_from(UsageRecord))
    barrier=Barrier(2)
    def read_on_separate_connection():
        with TestClient(app,raise_server_exceptions=False) as other:
            other.headers['Authorization']='Bearer '+reader['accessToken']
            barrier.wait(timeout=10)
            return other.get(BASE+'/books/'+demo['bookId']+'/character-cards',params={'branchId':demo['branchId'],'anchor':json.dumps(at)})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(read_on_separate_connection) for _ in range(2)]
        responses=[f.result(timeout=20) for f in futures]
    assert all(r.status_code==200 for r in responses),[r.text for r in responses]
    mapped=[{c['id']:(c['identityId'],c['scopeRevisionId'],c['scopeVersion']) for c in r.json()['items']} for r in responses]
    assert mapped[0]==mapped[1] and mapped[0]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CharacterIdentity))==len(mapped[0])
        assert db.scalar(select(func.count()).select_from(CharacterAlias))==len(mapped[0])
        assert db.scalar(select(func.count()).select_from(CharacterScopeRevision))==len(mapped[0])
        assert db.scalar(select(func.count()).select_from(UsageRecord))==before


def test_world_delete_removes_scoped_facts_but_published_identity_remains(client,reader,demo,profile):
    _,cards=indexed(client,reader,demo,profile)
    identity=next(c for c in cards if c['name']=='林渡')['identityId']
    world,line=start(client,reader,demo,profile)
    publication=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},'retain-public-uuid')
    published={'bookId':publication['bookId'],'branchId':publication['branchId'],'revisionId':publication['revisionId']}
    index(client,published,key='retained-identity-index')
    at=read_chapter(client,published)['blocks'][-1]['endAnchor']
    with SessionLocal() as db:
        progress=db.scalar(select(Progress).where(Progress.branch_id==published['branchId']))
        version=progress.version if progress else 0
    observe(client,reader,at,expected=version,sequence=2)
    before_card=next(c for c in card_list(client,published,at).json()['items'] if c['name']=='林渡')
    assert before_card['identityId']==identity
    with SessionLocal() as db:
        before=db.scalar(select(func.count()).select_from(UsageRecord))
        assert db.scalar(select(func.count()).select_from(CharacterScopeRevision).where(CharacterScopeRevision.scope_id==world['id']))>0
    deleted=client.delete(API+'/worlds/'+world['id'])
    assert deleted.status_code==200,deleted.text
    assert client.get(API+'/worlds/'+world['id']).status_code==404
    after_card=next(c for c in card_list(client,published,at).json()['items'] if c['name']=='林渡')
    assert after_card['identityId']==identity and after_card['scopeRevisionId']==before_card['scopeRevisionId']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CharacterScopeRevision).where(CharacterScopeRevision.scope_kind=='world',CharacterScopeRevision.scope_id==world['id']))==0
        assert db.scalar(select(func.count()).select_from(CharacterAlias).where(CharacterAlias.scope_kind=='world',CharacterAlias.scope_id==world['id']))==0
        assert db.get(CharacterIdentity,identity) and db.get(CharacterScopeRevision,after_card['scopeRevisionId'])
        assert db.scalar(select(func.count()).select_from(UsageRecord))==before


def test_world_created_before_character_index_keeps_same_uuid_when_cards_appear(client,reader,demo,profile):
    world,line=start(client,reader,demo,profile)
    before=next(c for c in world['characters'] if c['name']=='林渡')['identityId']
    _,cards=indexed(client,reader,demo,profile)
    after=next(c for c in cards if c['name']=='林渡')['identityId']
    assert before==after


def test_scope_archive_stores_same_secret_projection_as_public_checkpoint(client,reader,profile):
    from copy import deepcopy
    import json
    from app.models import CharacterScopeRevision
    from app.world.models import WorldCheckpoint
    from app.world.state import checkpoint_hash
    from tests.test_world_standalone import create,begin
    from tests.test_world import API
    made=create(client);world,line=begin(client,made)
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,line['headCheckpointId'])
        secret=next(l['text'] for l in cp.definition['lore'] if l['id'] not in cp.state['known'])
        definition=deepcopy(cp.definition)
        target=next(e for e in definition['entities'] if e['type']=='character' and e['id']!=cp.state['playerId'] and e['id'] in cp.state['discovered'])
        target['description']=secret+'的暗示';target['knownBy']=[]
        target_id=target['id'];cp.definition=definition
        cp.content_hash=checkpoint_hash(cp.state,definition,cp.text,cp.parent_checkpoint_id);db.commit()
    visible=client.get(API+f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}")
    assert visible.status_code==200 and secret not in visible.text
    scope=next(e for e in visible.json()['characterScopes'] if e['entityId']==target_id)
    with SessionLocal() as db:
        archived=db.get(CharacterScopeRevision,scope['scopeRevisionId'])
        assert secret not in json.dumps(archived.visible_data,ensure_ascii=False)


def test_precise_literal_source_point_uses_utf16_and_citation_length_independently(client,reader,demo):
    from app.character_identity import world_identity,reader_identity
    quote="😀林渡登上渡船。";block=uid()
    reader_snapshot=SimpleNamespace(owner_id=reader["user"]["id"],book_id=demo["bookId"],branch_id=demo["branchId"],data={
        "identityProofVersion":2,"characters":[{"id":"local-reader","displayName":"林渡","evidenceIds":["proof"]}],
        "evidence":[{"id":"proof","quote":quote,"anchor":{"blockId":block,"start":0,"end":len(quote.encode("utf-16-le"))//2}}]})
    entity={"id":"local-world","type":"character","name":"林渡","source":"original","aliases":[],"evidence":[{"blockId":block,"quote":"林渡"}]}
    world=SimpleNamespace(owner_id=reader["user"]["id"],id=uid(),source={"anchor":{"bookId":demo["bookId"],"branchId":demo["branchId"]},
        "blocks":[{"blockId":block,"text":quote}],"characterRefs":[]},definition={"entities":[entity]})
    with SessionLocal() as db:
        first=world_identity(db,world,entity)
        later=reader_identity(db,reader_snapshot,reader_snapshot.data["characters"][0])
        assert first.id==later.id and later.source_ref["start"]==2
        repeated={**entity,"id":"ambiguous","evidence":[{"blockId":block,"quote":"林渡"}]}
        ambiguous=SimpleNamespace(owner_id=world.owner_id,id=uid(),source={**world.source,"blocks":[{"blockId":block,"text":"林渡遇见另一位林渡"}]},definition={"entities":[repeated]})
        assert world_identity(db,ambiguous,repeated).id!=first.id

def test_migration_retains_published_role_uuid_after_original_source_is_physically_removed(client,reader,demo,profile):
    _,cards=indexed(client,reader,demo,profile)
    original=next(c for c in cards if c['name']=='林渡')['identityId']
    world,line=start(client,reader,demo,profile)
    publication=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},'migration-independent-publication')
    published={'bookId':publication['bookId'],'branchId':publication['branchId'],'revisionId':publication['revisionId']}
    index(client,published,key='migration-independent-index')
    at=read_chapter(client,published)['blocks'][-1]['endAnchor']
    with SessionLocal() as db:
        progress=db.scalar(select(Progress).where(Progress.branch_id==published['branchId']))
    observe(client,reader,at,expected=progress.version,sequence=2)
    selected=next(c for c in card_list(client,published,at).json()['items'] if c['name']=='林渡')
    assert selected['identityId']==original
    session=client.post(BASE+'/books/'+published['bookId']+'/character-chats',json={'anchor':at,'characterId':original},headers={'Idempotency-Key':'migrated-public-chat'})
    assert session.status_code==201,session.text
    role_id=session.json()['id']
    removed=client.request('DELETE',BASE+'/books/'+demo['bookId'],json={'expectedVersion':1},headers={'Idempotency-Key':'migration-remove-source'})
    assert removed.status_code==202,removed.text
    with SessionLocal() as db:
        assert db.get(Book,demo['bookId']) is None
        old_blocks=[(b.id,b.text,b.content_hash) for b in db.scalars(select(ContentBlock).where(ContentBlock.book_id==published['bookId']).order_by(ContentBlock.id))]
        role=db.get(RoleSession,role_id);old_role=(role.character_id,deepcopy(role.pinned_anchor))
        old_cp=db.get(WorldCheckpoint,line['headCheckpointId']);old_hash=old_cp.content_hash
        old_usage=db.scalar(select(func.count()).select_from(UsageRecord))
    cfg=Config('alembic.ini');command.downgrade(cfg,'0006')
    try:
        command.upgrade(cfg,'head')
        with SessionLocal() as db:
            role=db.get(RoleSession,role_id)
            assert role.character_identity_id==original and (role.character_id,role.pinned_anchor)==old_role
            assert [(b.id,b.text,b.content_hash) for b in db.scalars(select(ContentBlock).where(ContentBlock.book_id==published['bookId']).order_by(ContentBlock.id))]==old_blocks
            assert db.get(WorldCheckpoint,line['headCheckpointId']).content_hash==old_hash
            assert db.scalar(select(func.count()).select_from(UsageRecord))==old_usage
        again=next(c for c in card_list(client,published,at).json()['items'] if c['name']=='林渡')
        assert again['identityId']==original
        restored=client.get(API+'/worlds/'+world['id']).json()
        assert next(c for c in restored['characters'] if c['name']=='林渡')['identityId']==original
    finally:command.upgrade(cfg,'head')


def detached_legacy_role(client, reader, demo, profile):
    """Actual historical same-book publication, then owner HTTP source deletion."""
    from .test_book_deletion import legacy_publication
    owner = reader['user']['id']
    _, original_cards = indexed(client, reader, demo, profile)
    expected = next(c for c in original_cards if c['name'] == '林渡')['identityId']
    world, line = start(client, reader, demo, profile)
    with SessionLocal() as db:
        branch, revision, _, _ = legacy_publication(db, owner, demo, world['id'])
        published = {'bookId': demo['bookId'], 'branchId': branch.id, 'revisionId': revision.id}
        db.commit()
    index(client, published, key='legacy-role-index')
    at = read_chapter(client, published)['blocks'][-1]['endAnchor']
    observe(client, reader, at)
    chosen = next(c for c in card_list(client, published, at).json()['items'] if c['name'] == '林渡')
    assert chosen['identityId'] == expected
    role = client.post(BASE + '/books/' + demo['bookId'] + '/character-chats', json={
        'anchor': at, 'characterId': expected}, headers={'Idempotency-Key': 'legacy-retained-role'})
    assert role.status_code == 201, role.text
    with SessionLocal() as db:
        old_aliases = {a.entity_id: a.identity_id for a in db.scalars(select(CharacterAlias).where(
            CharacterAlias.owner_id == owner, CharacterAlias.scope_kind == 'reader',
            CharacterAlias.scope_id == published['branchId']))}
    removed = client.request('DELETE', BASE + '/books/' + demo['bookId'], json={'expectedVersion': 1},
        headers={'Idempotency-Key': 'legacy-role-source-delete'})
    assert removed.status_code == 202, removed.text
    carrier = removed.json()['retainedStories'][0]['bookId']
    with SessionLocal() as db:
        session = db.get(RoleSession, role.json()['id'])
        assert session.book_id == carrier and session.status == 'needs_refresh'
        assert session.character_identity_id == expected
        assert not db.scalar(select(func.count()).select_from(KnowledgeSnapshot))
        source = db.get(SourceDocument, db.get(Book, carrier).source_id)
        bindings = source.source_map['characterAliasBindings']
        assert all(set(ref) == {'branchId', 'entityId', 'identityId'} for ref in bindings)
        assert {r['entityId']: r['identityId'] for r in bindings if r['branchId'] == published['branchId']} == old_aliases
        old = {'role': deepcopy((session.character_id, session.pinned_anchor, session.status)),
            'aliases': old_aliases, 'branchId': published['branchId'],
            'blocks': [(b.id, b.text, b.content_hash) for b in db.scalars(select(ContentBlock).where(
                ContentBlock.book_id == carrier).order_by(ContentBlock.id))],
            'checkpoint': deepcopy((db.get(WorldCheckpoint, line['headCheckpointId']).state,
                db.get(WorldCheckpoint, line['headCheckpointId']).content_hash)),
            'usage': db.scalar(select(func.count()).select_from(UsageRecord))}
    return carrier, role.json()['id'], expected, world, line, old


def test_detached_legacy_publication_role_keeps_uuid_through_real_0007_roundtrip(client,reader,demo,profile):
    carrier, role_id, expected, world, line, old = detached_legacy_role(client, reader, demo, profile)
    cfg = Config('alembic.ini')
    command.downgrade(cfg, '0006')
    try:
        command.upgrade(cfg, 'head')
        with SessionLocal() as db:
            session = db.get(RoleSession, role_id)
            assert session.character_identity_id == expected
            assert (session.character_id, session.pinned_anchor, session.status) == old['role']
            assert {a.entity_id: a.identity_id for a in db.scalars(select(CharacterAlias).where(
                CharacterAlias.owner_id == reader['user']['id'], CharacterAlias.scope_kind == 'reader',
                CharacterAlias.scope_id == old['branchId']))} == old['aliases']
            assert [(b.id, b.text, b.content_hash) for b in db.scalars(select(ContentBlock).where(
                ContentBlock.book_id == carrier).order_by(ContentBlock.id))] == old['blocks']
            cp = db.get(WorldCheckpoint, line['headCheckpointId'])
            assert (cp.state, cp.content_hash) == old['checkpoint']
            assert db.scalar(select(func.count()).select_from(UsageRecord)) == old['usage']
        restored = client.get(API + '/worlds/' + world['id'])
        assert restored.status_code == 200, restored.text
        assert next(c for c in restored.json()['characters'] if c['name'] == '林渡')['identityId'] == expected
    finally:
        command.upgrade(cfg, 'head')


def test_preexisting_detached_role_without_durable_identity_ref_rejects_lossy_downgrade_before_ddl(client,reader,demo,profile):
    carrier, role_id, expected, _, _, old = detached_legacy_role(client, reader, demo, profile)
    with SessionLocal() as db:
        source = db.get(SourceDocument, db.get(Book, carrier).source_id)
        source.source_map = {k: v for k, v in source.source_map.items() if k != 'characterAliasBindings'}
        db.commit()
    with engine.connect() as connection:
        previous_version = connection.scalar(text('SELECT version_num FROM alembic_version'))
    try:
        with pytest.raises(RuntimeError, match='stable character identity'):
            command.downgrade(Config('alembic.ini'), '0006')
    finally:
        command.upgrade(Config('alembic.ini'), 'head')
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == previous_version
    with SessionLocal() as db:
        session = db.get(RoleSession, role_id)
        assert session.character_identity_id == expected
        assert (session.character_id, session.pinned_anchor, session.status) == old['role']
        assert db.get(CharacterIdentity, expected)


def test_unindexed_source_publication_prefix_keeps_world_identity_before_generated_chapter(client,reader,demo,profile):
    world,line=start(client,reader,demo,profile)
    identity=next(c for c in world['characters'] if c['name']=='林渡')['identityId']
    with SessionLocal() as db:
        assert db.get(World,world['id']).source['characterRefs']==[]
        assert db.scalar(select(func.count()).select_from(KnowledgeSnapshot))==0
    saved=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},'unindexed-prefix-publication')
    published={'bookId':saved['bookId'],'branchId':saved['branchId'],'revisionId':saved['revisionId']}
    index(client,published,key='unindexed-prefix-index')
    with SessionLocal() as db:
        progress=db.scalar(select(Progress).where(Progress.branch_id==published['branchId']))
        before=deepcopy((progress.anchor,progress.max_observed_anchor,progress.version))
        position=deepcopy(progress.anchor)
        old_head=db.get(WorldCheckpoint,line['headCheckpointId']).content_hash
        calls=db.scalar(select(func.count()).select_from(UsageRecord))
    first=next(c for c in card_list(client,published,position).json()['items'] if c['name']=='林渡')
    assert first['identityId']==identity
    assert next(c for c in card_list(client,published,position).json()['items'] if c['name']=='林渡')['identityId']==identity
    with SessionLocal() as db:
        progress=db.scalar(select(Progress).where(Progress.branch_id==published['branchId']))
        assert (progress.anchor,progress.max_observed_anchor,progress.version)==before
        assert db.get(WorldCheckpoint,line['headCheckpointId']).content_hash==old_head
        assert db.scalar(select(func.count()).select_from(UsageRecord))==calls


def test_publication_binding_cannot_merge_ambiguous_homonyms_or_their_scoped_facts(client,reader,demo):
    from app.character_identity import bind
    with SessionLocal() as db:
        source=db.get(SourceDocument,db.get(Book,demo['bookId']).source_id)
        block=db.scalar(select(ContentBlock).where(ContentBlock.book_id==demo['bookId']).order_by(ContentBlock.created_at,ContentBlock.id))
        # This real source paragraph names 林渡; both analyzed people have the
        # same broad evidence, so neither has a unique literal occurrence.
        assert '林渡' in block.text
        known=bind(db,reader['user']['id'],'world',uid(),'known-person')
        start=len(block.text[:block.text.index('林渡')].encode('utf-16-le'))//2
        source.source_map={**source.source_map,'characterBindings':[{'name':'林渡','identityId':known.id,
            'blockId':block.id,'start':start,'end':start+2}]}
        a={'id':'ambiguous-one','displayName':'林渡','evidenceIds':['e'],'identityProofVersion':2}
        b={**a,'id':'ambiguous-two'}
        snapshot=SimpleNamespace(owner_id=reader['user']['id'],book_id=demo['bookId'],branch_id=demo['branchId'],
            revision_id=demo['revisionId'],boundary={},scope_hash=uid(),data={'identityProofVersion':2,'characters':[a,b],
            'evidence':[{'id':'e','quote':block.text,'anchor':{'blockId':block.id,'start':0,'end':len(block.text.encode('utf-16-le'))//2}}]})
        first=reader_fields(db,snapshot,a,{'id':a['id'],'name':'第一位'})
        second=reader_fields(db,snapshot,b,{'id':b['id'],'name':'第二位'})
        assert first['identityId']!=second['identityId']
        assert known.id not in {first['identityId'],second['identityId']}
        assert first['scopeRevisionId']!=second['scopeRevisionId']
        assert db.get(CharacterScopeRevision,first['scopeRevisionId']).visible_data['id']==a['id']
        assert db.get(CharacterScopeRevision,second['scopeRevisionId']).visible_data['id']==b['id']


def test_migration_publication_binding_keeps_ambiguous_people_separate(client,reader,demo,profile):
    index(client,demo)
    with SessionLocal() as db:
        snapshot=db.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.branch_id==demo['branchId']))
        data=deepcopy(snapshot.data)
        original=next(c for c in data['characters'] if c['displayName']=='林渡')
        a={**original,'id':'ambiguous-one'};b={**original,'id':'ambiguous-two'}
        data['characters']=[a,b]
        snapshot.data=data
        proof=next(e for e in data['evidence'] if e['id']==a['evidenceIds'][0])
        start=proof['anchor']['start']+len(proof['quote'][:proof['quote'].index('林渡')].encode('utf-16-le'))//2
        source=db.get(SourceDocument,db.get(Book,demo['bookId']).source_id)
        source.source_map={**source.source_map,'characterBindings':[{'identityId':uid(),'name':'林渡',
            'blockId':proof['anchor']['blockId'],'start':start,'end':start+2}]}
        db.commit()
    cfg=Config('alembic.ini');command.downgrade(cfg,'0006')
    try:
        command.upgrade(cfg,'head')
        with SessionLocal() as db:
            aliases=list(db.scalars(select(CharacterAlias).where(CharacterAlias.scope_id==demo['branchId'])))
            assert {alias.entity_id for alias in aliases}=={'ambiguous-one','ambiguous-two'}
            assert len({alias.identity_id for alias in aliases})==2
            snapshot=db.get(KnowledgeSnapshot,snapshot.id)
            assert reader_identity(db,snapshot,a).id!=reader_identity(db,snapshot,b).id
    finally:command.upgrade(cfg,'head')


def test_downgrade_never_silently_changes_previously_exposed_world_proof_identity(client,reader,demo,profile):
    world,line=start(client,reader,demo,profile)
    original=next(c for c in world['characters'] if c['name']=='林渡')['identityId']
    with SessionLocal() as db:
        stored=db.get(World,world['id'])
        incoming=deepcopy(stored.definition)
        person=deepcopy(next(e for e in incoming['entities'] if e['name']=='林渡'))
        person['id']='later-homonym'
        incoming['entities'].append(person)
        incoming['initial'][person['id']]=deepcopy(incoming['initial'].get(next(e['id'] for e in incoming['entities'] if e['name']=='林渡' and e['id']!=person['id']),{}))
        stored.definition=merge(stored.definition,incoming)
        stored.status='preparing'
        preserved=deepcopy((stored.source,stored.definition))
        old_cp=db.get(WorldCheckpoint,line['headCheckpointId']).content_hash
        db.commit()
    before=client.get(API+'/worlds/'+world['id']).json()
    first_id=next(c for c in before['characters'] if c['name']=='林渡' and c['id']!='later-homonym')['identityId']
    assert first_id==original
    cfg=Config('alembic.ini')
    with engine.connect() as connection:
        previous_version = connection.scalar(text('SELECT version_num FROM alembic_version'))
    try:
        try:command.downgrade(cfg,'0006')
        except RuntimeError as error:
            assert 'stable character identity' in str(error).lower()
            with SessionLocal() as db:
                assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==previous_version
                assert db.get(CharacterIdentity,original)
        else:
            command.upgrade(cfg,'head')
        after=client.get(API+'/worlds/'+world['id']).json()
        assert next(c for c in after['characters'] if c['name']=='林渡' and c['id']!='later-homonym')['identityId']==original
        with SessionLocal() as db:
            stored=db.get(World,world['id'])
            assert (stored.source,stored.definition)==preserved
            assert db.get(WorldCheckpoint,line['headCheckpointId']).content_hash==old_cp
    finally:command.upgrade(cfg,'head')


def explicit_later_world(client,reader,demo,profile):
    _,cards=indexed(client,reader,demo,profile)
    known=next(c for c in cards if c['name']=='林渡')['identityId']
    world,line=start(client,reader,demo,profile)
    with SessionLocal() as db:
        stored=db.get(World,world['id'])
        ref=next(r for r in stored.source['characterRefs'] if r['identityId']==known)
        later=next(p for p in ref['proofs'] if p!=ref['proof'])
        block=next(b for b in stored.source['blocks'] if b['blockId']==later['blockId'])
        evidence=next(e for e in ref['evidence'] if e['blockId']==block['blockId'] and '林渡' in e['quote'])
        updated=deepcopy(stored.definition)
        actor=next(e for e in updated['entities'] if e['name']=='林渡')
        actor.update(sourceCharacterId=known,evidence=[deepcopy(evidence)])
        stored.definition=updated;db.commit()
    response=client.get(API+'/worlds/'+world['id'])
    assert response.status_code==200,response.text
    assert next(c for c in response.json()['characters'] if c['name']=='林渡')['identityId']==known
    return world,line,known


def test_valid_explicit_later_evidence_world_supports_lossless_migration_roundtrip(client,reader,demo,profile):
    world,line,known=explicit_later_world(client,reader,demo,profile)
    with SessionLocal() as db:
        stored=db.get(World,world['id']);cp=db.get(WorldCheckpoint,line['headCheckpointId'])
        original=deepcopy((stored.source,stored.definition,cp.state,cp.definition,cp.content_hash))
        calls=db.scalar(select(func.count()).select_from(UsageRecord))
    cfg=Config('alembic.ini')
    try:
        command.downgrade(cfg,'0006')
        assert client.get('/health/ready').status_code==503
        command.upgrade(cfg,'head')
        response=client.get(API+'/worlds/'+world['id'])
        assert response.status_code==200,response.text
        assert next(c for c in response.json()['characters'] if c['name']=='林渡')['identityId']==known
        with SessionLocal() as db:
            stored=db.get(World,world['id']);cp=db.get(WorldCheckpoint,line['headCheckpointId'])
            assert (stored.source,stored.definition,cp.state,cp.definition,cp.content_hash)==original
            assert db.scalar(select(func.count()).select_from(UsageRecord))==calls
    finally:command.upgrade(cfg,'head')


def test_explicit_same_person_later_and_first_proofs_publish_prefix_with_same_uuid(client,reader,demo,profile):
    world,line,known=explicit_later_world(client,reader,demo,profile)
    saved=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},'explicit-later-prefix')
    published={'bookId':saved['bookId'],'branchId':saved['branchId'],'revisionId':saved['revisionId']}
    index(client,published,key='explicit-later-prefix-index')
    with SessionLocal() as db:
        position=db.scalar(select(Progress).where(Progress.branch_id==published['branchId'])).anchor
        source=db.get(SourceDocument,db.get(Book,published['bookId']).source_id)
        assert len([r for r in source.source_map['characterBindings'] if r['identityId']==known])>=2
    card=next(c for c in card_list(client,published,position).json()['items'] if c['name']=='林渡')
    assert card['identityId']==known


def test_checkpoint_scope_archive_projects_new_identity_metadata_like_actual_public_entity(client,reader,demo):
    from app.world.models import WorldLine
    from app.world.state import initial_state,checkpoint_hash
    from .test_world import definition
    definition_value=definition();proof_id=uid()
    npc=next(e for e in definition_value['entities'] if e['id']=='npc')
    npc.update(source='original',aliases=['Elara'],evidence=[{'blockId':proof_id,'quote':'Elara'}])
    definition_value['lore'][0]['text']='Elara'
    definition_value,state=initial_state(definition_value,'player')
    owner=reader['user']['id'];world_id,line_id,cp_id=uid(),uid(),uid()
    with SessionLocal() as db:
        # Synthetic frozen-source/typed state fixture, not a claim that a
        # fictional source was imported or generated by an external provider.
        world=World(id=world_id,owner_id=owner,name='私密别名投影',source_book_id=demo['bookId'],source_title='合成来源',
            source={'kind':'book','anchor':{'bookId':demo['bookId'],'branchId':demo['branchId']},
                'blocks':[{'blockId':proof_id,'text':'Elara'}],'characterRefs':[]},definition=definition_value,status='active')
        db.add(world);db.flush()
        db.add(WorldLine(id=line_id,owner_id=owner,world_id=world_id,name='测试线',head_checkpoint_id=cp_id));db.flush()
        cp=WorldCheckpoint(id=cp_id,owner_id=owner,world_id=world_id,line_id=line_id,sequence=0,title='开场',text='旅人站在渡口。',
            state=state,definition=definition_value,content_hash=checkpoint_hash(state,definition_value,'旅人站在渡口。',None),kind='start')
        db.add(cp);world.active_line_id=line_id;db.commit()
        raw=deepcopy((world.source,world.definition,cp.state,cp.definition,cp.content_hash))
    response=client.get(API+f'/worlds/{world_id}/checkpoints/{cp_id}')
    assert response.status_code==200,response.text
    assert 'Elara' not in response.text
    entity=next(e for e in response.json()['state']['entities'] if e['id']=='npc')
    scope=next(e for e in response.json()['characterScopes'] if e['entityId']=='npc')
    with SessionLocal() as db:
        record=db.get(CharacterScopeRevision,scope['scopeRevisionId'])
        assert 'Elara' not in json.dumps(record.visible_data)
        assert record.visible_data=={**entity,**{k:v for k,v in scope.items() if k in {'identityId','sourceRef'}}}
        assert db.get(CharacterIdentity,scope['identityId']).source_ref['originalLabel']=='Elara'
        stored=db.get(World,world_id);cp=db.get(WorldCheckpoint,cp_id)
        assert (stored.source,stored.definition,cp.state,cp.definition,cp.content_hash)==raw
