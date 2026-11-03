"""Validate restored reader recognition revisions before any domain commit."""
from ..character_correction_schemas import CharacterRecognitionInput
from ..domain import canonical, utf16_slice
from ..schemas import Anchor
from .errors import ArchiveError


def _invalid():
    raise ArchiveError("BACKUP_READER_CORRECTION_INVALID")


def _shape(row):
    context,value=row.context,row.visible_data
    if row.scope_kind!="reader" or type(context.get("readerCorrection")) is not bool or context["readerCorrection"] is not True:
        _invalid()
    if set(context)!={"readerCorrection","bookId","readerBranchId","revisionId","anchor"} or type(row.version) is not int or row.version<1:
        _invalid()
    required={"id","displayName","aliases","evidenceIds","identityStatus","reason"}
    if not isinstance(value,dict) or set(value) not in (required,required|{"proofs"}):
        _invalid()
    if not isinstance(value["id"],str) or not 0<len(value["id"])<=100:
        _invalid()
    try:
        CharacterRecognitionInput.model_validate({"anchor":context["anchor"],"expectedVersion":row.version-1,
            **{key:item for key,item in value.items() if key not in {"id","proofs"}}},strict=True)
    except Exception:
        _invalid()
    if "proofs" in value:
        proofs=value["proofs"]
        if not isinstance(proofs,list) or len(proofs)!=len(value["evidenceIds"]):_invalid()
        for proof in proofs:
            if not isinstance(proof,dict) or set(proof)!={"quote","anchor"} or not isinstance(proof["quote"],str) or not proof["quote"]:
                _invalid()
            try:Anchor.model_validate(proof["anchor"],strict=True)
            except Exception:_invalid()
    labels=value["aliases"]
    if (not value["displayName"].strip() or not value["reason"].strip() or
        not labels or value["displayName"] not in labels or len(labels)!=len(set(labels)) or
        any(not label.strip() or label!=label.strip() or len(label)>100 for label in labels) or
        len(value["evidenceIds"])!=len(set(value["evidenceIds"]))):
        _invalid()


def _evidence(rows,snapshot,character,value,*,translate=False):
    evidence={item["id"]:item for item in snapshot.data.get("evidence",[])}
    allowed=set(character.get("evidenceIds",[])) & evidence.keys()
    selected=value["evidenceIds"]
    if translate and "proofs" in value:
        selected=[]
        for proof in value["proofs"]:
            matching=[identifier for identifier in allowed if evidence[identifier].get("quote")==proof["quote"] and
                evidence[identifier].get("anchor")==proof["anchor"]]
            if not matching:_invalid()
            selected.append(sorted(matching)[0])
    if not set(selected).issubset(allowed):
        _invalid()
    if "proofs" in value and any(evidence[identifier].get("quote")!=proof["quote"] or
        evidence[identifier].get("anchor")!=proof["anchor"] for identifier,proof in zip(selected,value["proofs"])):_invalid()
    revision=rows["revision"][snapshot.revision_id]
    locations={block:(ci,pi) for ci,chapter in enumerate(revision.manifest) for pi,block in enumerate(chapter["blocks"])}
    boundary=snapshot.boundary
    limit=(*locations[boundary["blockId"]],boundary["end"])
    quotes=[]
    for identifier in selected:
        item=evidence[identifier];anchor=item.get("anchor",{})
        if any(anchor.get(key)!=boundary.get(key) for key in ("bookId","branchId","revisionId")) or anchor.get("blockId") not in locations:
            _invalid()
        if (*locations[anchor["blockId"]],anchor["end"])>limit:
            _invalid()
        block=rows["paragraph"][anchor["blockId"]]
        quote=utf16_slice(block.text,anchor["start"],anchor["end"])
        if item.get("quote")!=quote:
            _invalid()
        quotes.append(quote)
    if any(not any(label in quote for quote in quotes) for label in value["aliases"]):
        _invalid()


def _same_identity(rows,row,snapshot,character):
    return any(alias.owner_id==row.owner_id==snapshot.owner_id and alias.scope_kind=="reader" and
        alias.scope_id==snapshot.branch_id==row.scope_id and alias.entity_id==character["id"] and
        alias.identity_id==row.identity_id for alias in rows.get("character_alias",{}).values())


def _translated_value(rows,row):
    value=row.visible_data
    if "proofs" in value:return value
    # Legacy six-field history is immutable. Only its original, exact frozen
    # scope/alias may provide quotes, still validated against actual paragraphs.
    options={}
    for snapshot in rows.get("knowledge_snapshot",{}).values():
        if (snapshot.owner_id!=row.owner_id or snapshot.branch_id!=row.scope_id or
            snapshot.book_id!=row.context["bookId"] or snapshot.revision_id!=row.context["revisionId"] or
            snapshot.boundary!=row.context["anchor"]):continue
        character=next((c for c in snapshot.data.get("characters",[]) if c.get("id")==value["id"]),None)
        if not character or not _same_identity(rows,row,snapshot,character):continue
        try:
            _evidence(rows,snapshot,character,value)
            evidence={e["id"]:e for e in snapshot.data["evidence"]}
            proofs=[{"quote":evidence[e]["quote"],"anchor":evidence[e]["anchor"]} for e in value["evidenceIds"]]
            options[canonical(proofs)]=proofs
        except ArchiveError:continue
    if len(options)!=1:_invalid()
    return {**value,"proofs":next(iter(options.values()))}


def validate_reader_corrections(rows):
    """Private backups may retain any scope; current Progress is not authority.

    Corrections must instead be backed by the exact frozen snapshot, original
    quote and stable local-entity binding captured in that private scope.
    """
    try:
        corrections=[];keys=set()
        for row in rows.get("character_scope_revision",{}).values():
            if not isinstance(row.context,dict):_invalid()
            if "readerCorrection" not in row.context:continue
            _shape(row)
            context,value=row.context,row.visible_data
            branch=rows["branch"][row.scope_id];revision=rows["revision"][context["revisionId"]]
            if context["readerBranchId"]!=row.scope_id or branch.book_id!=context["bookId"] or revision.branch_id!=row.scope_id:
                _invalid()
            matching=[snapshot for snapshot in rows.get("knowledge_snapshot",{}).values() if
                snapshot.branch_id==row.scope_id and snapshot.book_id==context["bookId"] and
                snapshot.revision_id==context["revisionId"] and snapshot.boundary==context["anchor"]]
            characters=[(snapshot,character) for snapshot in matching for character in snapshot.data.get("characters",[])
                if character.get("id")==value["id"]]
            if not characters or not any(alias.scope_kind=="reader" and alias.scope_id==row.scope_id and
                alias.entity_id==value["id"] and alias.identity_id==row.identity_id for alias in rows.get("character_alias",{}).values()):
                _invalid()
            proved=False
            for snapshot,character in characters:
                try:
                    _evidence(rows,snapshot,character,value)
                    proved=True
                    break
                except ArchiveError:
                    continue
            if not proved:_invalid()
            key=(row.scope_id,canonical(context["anchor"]),row.identity_id,row.version)
            if key in keys:_invalid()
            keys.add(key);corrections.append(row)
        for snapshot in rows.get("knowledge_snapshot",{}).values():
            data=snapshot.data;applied=data.get("readerCorrectionApplied")
            if applied is None and "readerCorrectionVersion" not in data:
                if any("readerAliases" in character or "readerIdentityConfirmed" in character for character in data.get("characters",[])):_invalid()
                continue
            version=data.get("readerCorrectionVersion")
            if applied is not True or type(version) is not int or version<1:_invalid()
            represented=[]
            for character in data.get("characters",[]):
                if "readerAliases" not in character and "readerIdentityConfirmed" not in character:continue
                aliases=character.get("readerAliases");confirmed=character.get("readerIdentityConfirmed")
                uncertain=character.get("identityUncertain")
                if not isinstance(aliases,list) or type(confirmed) is not bool or type(uncertain) is not bool or uncertain is confirmed:
                    _invalid()
                own_version=character.get("readerCorrectionVersion")
                if own_version is not None and (type(own_version) is not int or not 1<=own_version<=version):_invalid()
                matches=[row for row in corrections if row.scope_id==snapshot.branch_id and row.context["anchor"]==snapshot.boundary and
                    _same_identity(rows,row,snapshot,character) and row.version<=version and (own_version is None or row.version==own_version) and
                    row.visible_data["displayName"]==character["displayName"] and row.visible_data["aliases"]==aliases and
                    (row.visible_data["identityStatus"]=="confirmed")==confirmed]
                if not matches:_invalid()
                proved=[]
                for row in matches:
                    try:
                        _evidence(rows,snapshot,character,_translated_value(rows,row),translate=True)
                        proved.append(row.version)
                    except ArchiveError:
                        continue
                if not proved:_invalid()
                represented.append(max(proved))
            if not represented or max(represented)!=version:_invalid()
    except ArchiveError:
        raise
    except Exception as error:
        raise ArchiveError("BACKUP_READER_CORRECTION_INVALID") from error
