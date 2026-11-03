"""Project reader analysis IDs in their branch scope; prose stays opaque."""
from copy import deepcopy
from uuid import uuid4

from .errors import ArchiveError


def remap_reader_local_ids(rows,id_map):
    originals={new:old for (kind,old),new in id_map.items() if kind=="branch"}
    used=set(id_map.values())|{old for (_,old) in id_map}
    scopes={}

    def scope(key):
        return scopes.setdefault(key,{"reader_entity":set(),"reader_evidence":set(),"reader_fact":set()})

    for kind in ("knowledge_snapshot","analysis_chunk"):
        for row in rows.get(kind,{}).values():
            names=scope(row.branch_id)
            for field,target in (("characters","reader_entity"),("evidence","reader_evidence"),("facts","reader_fact")):
                names[target].update(v["id"] for v in row.data.get(field,[]))
    for alias in rows.get("character_alias",{}).values():
        if alias.scope_kind=="reader":scope(alias.scope_id)["reader_entity"].add(alias.entity_id)
    for session in rows.get("role_session",{}).values():
        scope(session.branch_id)["reader_entity"].add(session.character_id)

    source_scopes={}
    for row in rows.get("world",{}).values():
        source=row.source;branch=(source.get("anchor") or {}).get("branchId")
        key=branch if branch in originals else "world-source:"+row.id
        source_scopes[row.id]=key
        scope(key)["reader_entity"].update(v["entityId"] for v in source.get("characterRefs",[]) if v.get("entityId"))

    maps={}
    for key,names in scopes.items():
        values={};original=originals.get(key,key)
        if key.startswith("world-source:"):
            old_world=next(old for (kind,old),new in id_map.items() if kind=="world" and new==key[13:])
            original="world-source:"+old_world
        for kind,identifiers in names.items():
            values[kind]={}
            for identifier in sorted(identifiers):
                if not isinstance(identifier,str) or not identifier:raise ArchiveError("BACKUP_READER_LOCAL_REFERENCE_INVALID")
                value=uuid4().hex
                while value in used:value=uuid4().hex
                used.add(value);values[kind][identifier]=value
                id_map[(kind,original+":"+identifier)]=value
        maps[key]=values

    def ref(key,kind,value):
        if value is None:return None
        try:return maps[key][kind][value]
        except KeyError as exc:raise ArchiveError("BACKUP_READER_LOCAL_REFERENCE_INVALID") from exc

    def refs(key,kind,values):return [ref(key,kind,value) for value in values]

    def analysis(key,data):
        data=deepcopy(data)
        for character in data.get("characters",[]):
            character["id"]=ref(key,"reader_entity",character["id"])
            character["evidenceIds"]=refs(key,"reader_evidence",character.get("evidenceIds",[]))
        for evidence in data.get("evidence",[]):evidence["id"]=ref(key,"reader_evidence",evidence["id"])
        for fact in data.get("facts",[]):
            fact["id"]=ref(key,"reader_fact",fact["id"])
            fact["subjectId"]=ref(key,"reader_entity",fact["subjectId"])
            fact["knownBy"]=refs(key,"reader_entity",fact.get("knownBy",[]))
            fact["evidenceIds"]=refs(key,"reader_evidence",fact.get("evidenceIds",[]))
            for claim in fact.get("knowledge",[]):
                claim["characterId"]=ref(key,"reader_entity",claim["characterId"])
                claim["evidenceIds"]=refs(key,"reader_evidence",claim.get("evidenceIds",[]))
        return data

    for kind in ("knowledge_snapshot","analysis_chunk"):
        for row in rows.get(kind,{}).values():row.data=analysis(row.branch_id,row.data)
    tasks = rows.get('task', {})
    for batch in rows.get('generation_batch', {}).values():
        task = tasks[batch.task_id]
        key = batch.origin_anchor['branchId']
        checkpoint = deepcopy(task.checkpoint)
        for chapters in checkpoint.get('proseFactLedger', {}).values():
            for ledger in chapters.values():
                for entry in ledger['entries']:
                    entry['subjectId'] = ref(key, 'reader_entity', entry['subjectId'])
        for chapters in checkpoint.get('proseEventLedger', {}).values():
            for ledger in chapters.values():
                from ..prose.events import validate
                try:validate(ledger, subjects=set(maps.get(key, {}).get('reader_entity', {})))
                except (ValueError,TypeError) as error:
                    raise ArchiveError('BACKUP_GENERATION_EVENTS_INVALID') from error
                for entry in ledger['events']:
                    entry['subjectId'] = ref(key, 'reader_entity', entry['subjectId'])
                    change = entry['change']
                    if entry['kind'] == 'item':
                        for field in ('fromHolderId','toHolderId'):change[field] = ref(key,'reader_entity',change[field])
                    elif entry['kind'] == 'knowledge':change['learnedByIds'] = refs(key,'reader_entity',change['learnedByIds'])
                    elif entry['kind'] == 'relationship':change['otherId'] = ref(key,'reader_entity',change['otherId'])
        task.checkpoint = checkpoint
    for alias in rows.get("character_alias",{}).values():
        if alias.scope_kind=="reader":alias.entity_id=ref(alias.scope_id,"reader_entity",alias.entity_id)
    for session in rows.get("role_session",{}).values():
        session.character_id=ref(session.branch_id,"reader_entity",session.character_id)
        if session.card_id:session.card_id=ref(session.branch_id,"reader_entity",session.card_id)
    for row in rows.get("resume_target", {}).values():
        context = deepcopy(row.context)
        if context.get("readerBranchId") is not None and context.get("characterId") is not None:
            context["characterId"] = ref(context["readerBranchId"], "reader_entity", context["characterId"])
            row.context = context
    for identity in rows.get("character_identity",{}).values():
        source=deepcopy(identity.source_ref)
        if source.get("kind")=="reader" and source.get("entityId"):
            key=source.get("branchId") or source.get("scopeId")
            if key in maps:source["entityId"]=ref(key,"reader_entity",source["entityId"])
        identity.source_ref=source
    for card in rows.get("character_scope_revision",{}).values():
        if card.scope_kind!="reader":continue
        value=deepcopy(card.visible_data)
        if value.get("id"):value["id"]=ref(card.scope_id,"reader_entity",value["id"])
        if "evidenceIds" in value:value["evidenceIds"]=refs(card.scope_id,"reader_evidence",value["evidenceIds"])
        card.visible_data=value
    for row in rows.get("world",{}).values():
        source=deepcopy(row.source);key=source_scopes[row.id]
        for value in source.get("characterRefs",[]):
            if value.get("entityId"):value["entityId"]=ref(key,"reader_entity",value["entityId"])
        row.source=source
