"""Prepare a new owned copy; this module cannot enqueue tasks or transfer credit.

Only declared ID/reference locations change. Names, prose, frozen world state
and anchors are never searched/replaced by string. The domain restore adapter
must map this plan inside its transaction, not blindly deserialize task rows.
"""
import copy
from dataclasses import dataclass
import re
from uuid import uuid4

from .errors import ArchiveError
from .manifest import ArchiveLimits, object_key, pointer_get, pointer_parts, pointer_set, validate_documents, validate_manifest

HISTORY_ONLY = frozenset({"task", "task_event", "stream_content", "historical_usage", "historical_profile"})
FORBIDDEN_FIELDS = frozenset({
    "apikey", "providerkey", "accesskey", "accesstoken", "refreshtoken", "password", "passwordhash",
    "credentialcipher", "credentials", "encryptedcredentials", "authorization", "providerjournal", "rawjournal",
    "journalpath", "accountbudget", "dailybudget", "dailycostlimit", "consentbudget", "remainingcredit",
    "keycipher", "keytail", "providermasterkey", "jwtsigningsecret", "refreshhash", "previoushashes",
    "callbudget", "inputbudget", "outputbudget", "callsreserved", "inputreserved", "outputreserved",
})
OWNER_FIELDS = frozenset({"ownerid", "userid", "accountid"})


def fictional_fields_path(row_type, parts):
    """Exact row-relative path to primitive story properties, never metadata."""
    if row_type=="WorldTurn" and len(parts)==8 and parts[:3]==("fields","result","chapters") and parts[3].isdecimal():
        return parts[4:6] in {("state","entities"),("definition","initial")} and parts[-1]=="fields"
    return (len(parts)==5 and parts[0]=="fields" and parts[4]=="fields" and
            ((row_type in {"World","WorldCheckpoint"} and parts[1:3]==("definition","initial")) or
             (row_type=="WorldCheckpoint" and parts[1:3]==("state","entities"))))


def authored_source_key(row_type, parts, key):
    """Author setting block IDs are local citations, never reader DB rows.

    Do not exempt whole objects: prose packing and private-field validation
    still inspect every setting block and all its other fields normally.
    """
    if key not in {'blockId','chapterId'}:return False
    if row_type in {'World','WorldCheckpoint'}:
        return len(parts)==5 and parts[:4]==('fields','definition','authoring','sourceBlocks') and parts[4].isdecimal()
    return (row_type=='WorldTurn' and len(parts)==8 and parts[:3]==('fields','result','chapters')
            and parts[3].isdecimal() and parts[4:7]==('definition','authoring','sourceBlocks') and parts[7].isdecimal())


def fictional_scalar(document, parts, child):
    """EntityInitial.fields is user story data, never account configuration."""
    if isinstance(child, (dict, list)) or len(parts) not in {8,11} or parts[0] != "rows" or parts[2] != "fields" or parts[-2] != "fields":
        return False
    rows = document.get("rows") if isinstance(document, dict) else None
    if not isinstance(rows, list) or not parts[1].isdecimal() or int(parts[1]) >= len(rows): return False
    row = rows[int(parts[1])]
    if not isinstance(row, dict): return False
    return fictional_fields_path(row.get("rowType"),parts[2:-1])


def validate_private_policy(manifest: dict, documents: dict):
    """Reject operational credentials/budgets and undeclared ownership fields.

    This supplements the authorized snapshot exporter; it is not an account
    authorization decision. User prose is opaque text and is not scanned/logged.
    """
    owners = {(item["path"], tuple(pointer_parts(item["ownerPointer"])))
              for item in manifest["objects"] if item.get("ownerPointer") is not None}
    for path, document in documents.items():
        pending = [((), document)]
        while pending:
            parts, value = pending.pop()
            if len(parts) > 256:
                raise ArchiveError("BACKUP_JSON_DEPTH_LIMIT")
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                    story_property = fictional_scalar(document, parts + (key,), child)
                    if normalized in FORBIDDEN_FIELDS and not story_property:
                        raise ArchiveError("BACKUP_PRIVATE_POLICY_INVALID")
                    if normalized in OWNER_FIELDS and not story_property and (path, parts + (key,)) not in owners:
                        raise ArchiveError("BACKUP_OWNER_UNDECLARED")
                    pending.append((parts + (key,), child))
            elif isinstance(value, list):
                pending.extend((parts + (str(index),), child) for index, child in enumerate(value))


@dataclass(frozen=True)
class RestoreObject:
    kind: str
    source_id: str
    new_id: str
    path: str
    id_pointer: str


@dataclass
class RestorePlan:
    target_owner: str
    documents: dict
    id_map: dict[tuple[str, str], str]
    active_objects: list[RestoreObject]
    historical_objects: list[RestoreObject]
    roots: list[dict]
    detached_sources: list[dict]
    # Explicit semantics for the adapter: historical usage is display-only;
    # imported task records have no claimable worker queue or budget reservation.
    replay_historical_tasks: bool = False
    transfer_billing_credit: bool = False


def build_restore_plan(manifest: dict, documents: dict, target_owner: str,
                       limits: ArchiveLimits = ArchiveLimits(), id_factory=None) -> RestorePlan:
    if not isinstance(target_owner, str) or not target_owner or len(target_owner) > 200:
        raise ArchiveError("BACKUP_TARGET_OWNER_INVALID")
    manifest = validate_manifest(manifest, limits)
    validate_documents(manifest, documents)
    validate_private_policy(manifest, documents)
    create_id = id_factory or (lambda: uuid4().hex)
    mapping, generated = {}, set()
    source_ids = {item["id"] for item in manifest["objects"]}
    for item in manifest["objects"]:
        identifier = create_id()
        if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None or identifier in generated or identifier in source_ids:
            raise ArchiveError("BACKUP_NEW_ID_INVALID")
        mapping[(item["type"], item["id"])] = identifier
        generated.add(identifier)
    restored = copy.deepcopy(documents)
    active, history = [], []
    for item in manifest["objects"]:
        identifier = mapping[(item["type"], item["id"])]
        pointer_set(restored[item["path"]], item.get("idPointer", "/id"), identifier)
        if item.get("ownerPointer") is not None:
            pointer_set(restored[item["path"]], item["ownerPointer"], target_owner)
        record = RestoreObject(item["type"], item["id"], identifier, item["path"], item.get("idPointer", "/id"))
        (history if item["type"] in HISTORY_ONLY else active).append(record)
    objects = {(item["type"], item["id"]): item for item in manifest["objects"]}
    for reference in manifest["references"]:
        origin = objects[object_key(reference["from"])]
        pointer_set(restored[origin["path"]], reference["field"], mapping[object_key(reference["to"])])
    detached = []
    for source in manifest["externalSources"]:
        item = copy.deepcopy(source)
        item["from"]["id"] = mapping[object_key(source["from"])]
        item["status"] = "source-not-imported"
        detached.append(item)
    roots = [{"type": root["type"], "id": mapping[object_key(root)]} for root in manifest["roots"]]
    return RestorePlan(target_owner, restored, mapping, active, history, roots, detached)
