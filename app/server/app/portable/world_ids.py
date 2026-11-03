"""Remap world-local structural IDs without searching names or narrative text.

Each world has a separate map, including historical definitions on every line.
The persistent world/line/checkpoint IDs have already been remapped by the
manifest plan. Arbitrary EntityInitial.fields values remain opaque story data.
"""
from copy import deepcopy
from uuid import uuid4

from .errors import ArchiveError
from .world_scenes import scenes
from ..domain import canonical,digest


def remap_world_local_ids(rows, id_map):
    original_worlds={new:old for (kind,old),new in id_map.items() if kind=="world"}
    used=set(id_map.values())|{old for (_,old) in id_map}
    maps={}
    for world_id,instance in rows.get("world",{}).items():
        definitions=[instance.definition]+[p.definition for p in rows.get("checkpoint",{}).values() if p.world_id==world_id]
        candidates=[value for turn in rows.get("world_turn",{}).values() if turn.world_id==world_id for value in scenes(turn.result)]
        definitions.extend(value["definition"] for value in candidates)
        names={"world_entity":set(),"world_lore":set(),"promise":set()}
        for definition in definitions:
            names["world_entity"].update(e["id"] for e in definition.get("entities",[]))
            names["world_lore"].update(e["id"] for e in definition.get("lore",[]))
        for point in rows.get("checkpoint",{}).values():
            if point.world_id==world_id:names["promise"].update(e["id"] for e in point.state.get("unresolved",[]))
        for value in candidates:names["promise"].update(e["id"] for e in value["state"].get("unresolved",[]))
        scope={}
        for kind,identifiers in names.items():
            mapping={}
            for old in sorted(identifiers):
                new=uuid4().hex
                while new in used or new in identifiers:new=uuid4().hex
                used.add(new);mapping[old]=new
                id_map[(kind,original_worlds[world_id]+":"+old)]=new
            scope[kind]=mapping
        maps[world_id]=scope

    for world_id,scope in maps.items():
        entity_ids,lore_ids,promises=scope["world_entity"],scope["world_lore"],scope["promise"]

        def ref(value,mapping,*,visibility=False):
            if value is None:return None
            if visibility and value in {"player","all"}:return value
            if visibility and value.startswith('entity:'):return 'entity:'+ref(value[7:],mapping)
            if value not in mapping:raise ArchiveError("BACKUP_WORLD_LOCAL_REFERENCE_INVALID")
            return mapping[value]

        def refs(value,mapping,*,visibility=False):
            return [ref(v,mapping,visibility=visibility) for v in value]

        def relation_key(value):
            for prefix in ("relation:","relationEvidence:","inventory:"):
                if value.startswith(prefix):return prefix+ref(value[len(prefix):],entity_ids)
            return value

        def entity_state(value):
            value=deepcopy(value)
            value["location"]=ref(value.get("location"),entity_ids)
            for key in ("inventory","relations","relationEvidence"):
                if key in value:value[key]={ref(k,entity_ids):v for k,v in value[key].items()}
            if "known" in value:value["known"]=refs(value["known"],lore_ids)
            return value

        def definition(value):
            value=deepcopy(value)
            if not value.get("entities"):return value  # a preparation-only world
            value["location"]=ref(value["location"],entity_ids)
            for entity in value["entities"]:
                entity["id"]=ref(entity["id"],entity_ids)
                entity["knownFacts"]=refs(entity.get("knownFacts",[]),lore_ids)
                entity["knownBy"]=refs(entity.get("knownBy",[]),entity_ids,visibility=True)
            for entry in value.get("lore",[]):
                entry["id"]=ref(entry["id"],lore_ids)
                entry["entityIds"]=refs(entry.get("entityIds",[]),entity_ids)
                entry["knownBy"]=refs(entry.get("knownBy",[]),entity_ids,visibility=True)
            value["initial"]={ref(key,entity_ids):entity_state(v) for key,v in value.get("initial",{}).items()}
            rules=value.get("rules",{})
            if "routes" in rules:rules["routes"]={ref(k,entity_ids):refs(v,entity_ids) for k,v in rules["routes"].items()}
            if value.get('script'):value['script']=script(value['script'])
            for opening in value.get('authoring',{}).get('openings',[]):
                if opening.get('characterId'):opening['characterId']=ref(opening['characterId'],entity_ids)
                if opening.get('locationId'):opening['locationId']=ref(opening['locationId'],entity_ids)
            return value

        def script(value):
            value=deepcopy(value)
            def condition(c):
                c=deepcopy(c)
                if c.get('entityId') not in (None,'player'):c['entityId']=ref(c['entityId'],entity_ids)
                for key in ('placeId','itemId','toId'):
                    if c.get(key) is not None:c[key]=ref(c[key],entity_ids)
                if c.get('loreId') is not None:c['loreId']=ref(c['loreId'],lore_ids)
                return c
            for entry in value.get('scenes',[]):
                entry['placeId']=ref(entry['placeId'],entity_ids)
                entry['characterIds']=refs(entry.get('characterIds',[]),entity_ids)
            for entry in value.get('objectives',[])+value.get('endings',[]):
                entry['checks']=[condition(c) for c in entry.get('checks',[])]
                for step in entry.get('settlementSteps',[]):step['checks']=[condition(c) for c in step['checks']]
                for t in entry.get('transitions',[]):
                    t['authorityId']=ref(t['authorityId'],entity_ids);t['placeId']=ref(t['placeId'],entity_ids)
                    t['checks']=[condition(c) for c in t['checks']]
                    t['effects']=semantic(t['effects'])
            for entry in value.get('castSchedule',[]):
                entry['entityId']=ref(entry['entityId'],entity_ids);entry['placeId']=ref(entry['placeId'],entity_ids)
            if value.get('recommendedCharacterId'):value['recommendedCharacterId']=ref(value['recommendedCharacterId'],entity_ids)
            value['conservedItemIds']=refs(value.get('conservedItemIds',[]),entity_ids)
            return value

        def transcript_segments(segments):
            result=deepcopy(segments)
            for segment in result:
                speaker=segment.get('speakerId')
                if speaker in entity_ids:segment['speakerId']=ref(speaker,entity_ids)
                elif speaker is not None:segment.pop('speakerId')
            return result

        def semantic(value):
            """Only declared event/memory/patch fields, never generic string IDs."""
            if isinstance(value,list):return [semantic(v) for v in value]
            if not isinstance(value,dict):return deepcopy(value)
            result={}
            for key,child in value.items():
                if key=='sceneTranscript' and isinstance(child,dict):
                    transcript=deepcopy(child)
                    # Old imported transcripts can retain obsolete script
                    # tokens. Preserve display text without guessing a person.
                    transcript['segments']=transcript_segments(transcript.get('segments',[]))
                    result[key]=transcript
                elif key in {'sceneSpatialContext','sceneObservations','scenePresence'} and isinstance(child,dict):
                    result[key]=semantic(child)
                    result[key]['location']=ref(child.get('location'),entity_ids)
                    if 'absentCharacterIds' in child:result[key]['absentCharacterIds']=refs(child['absentCharacterIds'],entity_ids)
                elif key=='sceneConversation' and isinstance(child,dict):
                    result[key]=semantic(child)
                    for entry,old_entry in zip(result[key].get('entries',[]),child.get('entries',[])):
                        entry['partnerId']=ref(old_entry['partnerId'],entity_ids)
                elif key=='sceneConnections' and isinstance(child,list):
                    result[key]=[{**edge,'from':ref(edge['from'],entity_ids),'to':ref(edge['to'],entity_ids)} for edge in child]
                elif key in {"entityId","speakerId","itemId","fromId","toId","counterpartyId","playerId","authorityId","placeId"}:
                    result[key]=ref(child,entity_ids)
                elif key in {"entityIds","discovered"}:result[key]=refs(child,entity_ids)
                elif key in {"visibleTo","knownBy"}:result[key]=refs(child,entity_ids,visibility=True)
                elif key in {"loreIds","knownFacts","learned"}:result[key]=refs(child,lore_ids)
                elif key=="resolvedUnresolved":result[key]=refs(child,promises)
                elif key in {"text","description","title","summary","reason","fields"}:result[key]=deepcopy(child)
                elif key=="sourceRef" and isinstance(child,dict) and child.get("kind")!="world":result[key]=deepcopy(child)
                elif key=="field" and isinstance(child,str):result[key]=relation_key(child)
                else:result[key]=semantic(child)
            # Patches store location and inventory references in before/after.
            if value.get("field")=="location":
                for key in ("before","after"):
                    if key in value:result[key]=ref(value[key],entity_ids)
            elif value.get("field")=="inventory":
                for key in ("before","after","value"):
                    if isinstance(value.get(key),dict):result[key]={ref(k,entity_ids):v for k,v in value[key].items()}
            elif value.get("field")=="known":
                for key in ("before","after","value"):
                    if key in value:result[key]=refs(value[key],lore_ids)
            if value.get("field")=="location" and "value" in value:result["value"]=ref(value["value"],entity_ids)
            return result

        def state(value):
            value=deepcopy(value)
            for key in ("playerId","location"):value[key]=ref(value[key],entity_ids)
            value["entities"]={ref(k,entity_ids):entity_state(v) for k,v in value["entities"].items()}
            value["known"]=refs(value.get("known",[]),lore_ids)
            value["discovered"]=refs(value.get("discovered",[]),entity_ids)
            value["queue"]=semantic(value.get("queue",[]))
            for unresolved in value.get("unresolved",[]):unresolved["id"]=ref(unresolved["id"],promises)
            observed={}
            for identifier,values in value.get("observed",{}).items():
                visible={}
                for key,child in values.items():
                    if key=="location":visible[key]=ref(child,entity_ids)
                    elif key=="known":visible[key]=refs(child,lore_ids)
                    elif key=="inventory" and isinstance(child,dict):visible[key]={ref(k,entity_ids):v for k,v in child.items()}
                    else:visible[relation_key(key)]=deepcopy(child)
                observed[ref(identifier,entity_ids)]=visible
            value["observed"]=observed
            if value.get('plot'):
                plot=value['plot']
                plot['acquired']={ref(who,entity_ids):{ref(item,entity_ids):quantity for item,quantity in items.items()}
                                  for who,items in plot.get('acquired',{}).items()}
                if 'visited' in plot:plot['visited']={ref(who,entity_ids):refs(places,entity_ids) for who,places in plot['visited'].items()}
                plot['appeared']=refs(plot.get('appeared',[]),entity_ids)
                for key in ('transfers','transitionReceipts'):
                    if key in plot:plot[key]=semantic(plot[key])
                if plot.get('resolution'):plot['resolution']['receiptRefs']=semantic(plot['resolution'].get('receiptRefs',[]))
            return value

        instance=rows["world"][world_id];instance.definition=definition(instance.definition)
        for appearance_kind in ('character_artwork','character_look'):
            for art in rows.get(appearance_kind,{}).values():
                if art.world_id==world_id:
                    art.entity_id=ref(art.entity_id,entity_ids)
                    if appearance_kind=='character_artwork':art.descriptor={**art.descriptor,'entityId':art.entity_id}
        for chat in rows.get('world_chat',{}).values():
            if chat.world_id==world_id:chat.character_id=ref(chat.character_id,entity_ids)
        for point in rows.get("checkpoint",{}).values():
            if point.world_id!=world_id:continue
            point.definition=definition(point.definition);point.state=state(point.state)
            point.changes=semantic(point.changes);point.memory=semantic(point.memory)
        for event in rows.get("world_event",{}).values():
            if event.world_id!=world_id:continue
            event.entity_id=ref(event.entity_id,entity_ids)
            event.visible_to=refs(event.visible_to,entity_ids,visibility=True);event.data=semantic(event.data)
        for turn in rows.get("world_turn",{}).values():
            if turn.world_id!=world_id:continue
            result=deepcopy(turn.result)
            for value in scenes(result):
                value["state"]=state(value["state"]);value["definition"]=definition(value["definition"])
                value["changes"]=semantic(value["changes"]);value["events"]=semantic(value["events"])
                if value.get('dialogueSegments') is not None:value['dialogueSegments']=semantic(value['dialogueSegments'])
                plan=semantic(value["plan"])
                if plan.get("location") is not None:plan["location"]=ref(plan["location"],entity_ids)
                for entity in plan.get("additions",[]):entity["id"]=ref(entity["id"],entity_ids)
                for entry in plan.get("newLore",[]):entry["id"]=ref(entry["id"],lore_ids)
                value["plan"]=plan
                value["contentHash"]=digest(canonical({k:v for k,v in value.items() if k!="contentHash"}))
            if result.get('draftSegments') is not None:result['draftSegments']=transcript_segments(result['draftSegments'])
            turn.result=result
        for alias in rows.get("character_alias",{}).values():
            if alias.scope_kind=="world" and alias.scope_id==world_id:alias.entity_id=ref(alias.entity_id,entity_ids)
        for identity in rows.get("character_identity",{}).values():
            source=identity.source_ref
            if source.get("kind")=="world" and source.get("worldId")==world_id and source.get("entityId"):
                identity.source_ref={**source,"entityId":ref(source["entityId"],entity_ids)}
        for revision in rows.get("character_scope_revision",{}).values():
            if revision.scope_kind!="world" or revision.scope_id!=world_id:continue
            visible=deepcopy(revision.visible_data)
            if visible.get("id") in entity_ids:visible["id"]=ref(visible["id"],entity_ids)
            if isinstance(visible.get("state"),dict):visible["state"]=entity_state(visible["state"])
            revision.visible_data=semantic(visible);revision.context=semantic(revision.context)
        for kind in ("resume_target", "playback_position"):
            for position in rows.get(kind, {}).values():
                context = deepcopy(position.context)
                if context.get("worldId") == world_id:
                    if context.get("characterId") is not None:
                        context["characterId"] = ref(context["characterId"], entity_ids)
                    position.context = context
        for kind,field in (("asset","origin_scope"),("asset_ref","scope")):
            for image in rows.get(kind,{}).values():
                scope_value=getattr(image,field)
                if scope_value.get("kind")=="world" and scope_value.get("worldId")==world_id and scope_value.get("entityId"):
                    setattr(image,field,{**scope_value,"entityId":ref(scope_value["entityId"],entity_ids)})
