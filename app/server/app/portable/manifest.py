"""Strict package inventory and typed reference closure; no name-based merging."""
import copy
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import PurePosixPath
import re
import unicodedata

from .errors import ArchiveError

SCHEMA_VERSION = 1
LOGICAL_TYPES = frozenset({
    'character_artwork','character_look',
    'world_chat', 'world_chat_message', 'play_control', 'play_memory_revision',
    "story", "book", "branch", "revision", "chapter", "paragraph", "progress", "note", "playback_position",
    "character_identity", "character_alias", "character_scope_revision", "reader_character", "role_session", "role_message",
    "source", "world", "world_line", "checkpoint", "world_event", "memory", "promise", "asset", "asset_ref",
    "generation_batch", "chapter_version", "generation_acceptance", "chapter_content", "stream_content", "task", "task_event", "historical_usage", "historical_profile",
    "resume_target", "knowledge_snapshot", "analysis_chunk", "candidate", "world_turn", "world_publication", "world_event_token", "asset_variant", "world_scene", "comparison_exposure",
})
MEDIA = {"data": {".json": "application/json"}, "body": {".txt": "text/plain"},
         "asset": {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}}


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 20000
    max_uncompressed_bytes: int = 2 * 1024**3
    max_image_bytes: int = 10 * 1024**2
    max_manifest_bytes: int = 32 * 1024**2
    max_json_bytes: int = 64 * 1024**2
    max_objects: int = 200000
    max_references: int = 500000


def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024 or value != unicodedata.normalize("NFC", value):
        raise ArchiveError("BACKUP_PATH_INVALID")
    if any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value) or any(char in value for char in '\\:<>"|?*') or value.startswith("/"):
        raise ArchiveError("BACKUP_PATH_INVALID")
    parts = value.split("/")
    for part in parts:
        if not part or part in {".", ".."} or len(part) > 255 or part.endswith((".", " ")):
            raise ArchiveError("BACKUP_PATH_INVALID")
        if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])", part.split(".")[0]):
            raise ArchiveError("BACKUP_PATH_INVALID")
    if PurePosixPath(value).as_posix() != value:
        raise ArchiveError("BACKUP_PATH_INVALID")
    return value


def _pairs(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ArchiveError("BACKUP_JSON_DUPLICATE_KEY")
        output[key] = value
    return output


def parse_json(raw: bytes, limit: int):
    if len(raw) > limit:
        raise ArchiveError("BACKUP_JSON_SIZE_LIMIT")
    def invalid_constant(_):
        raise ArchiveError("BACKUP_JSON_INVALID")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ArchiveError("BACKUP_JSON_INVALID") from error


def encode_json(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeEncodeError) as error:
        raise ArchiveError("BACKUP_JSON_INVALID") from error


def object_key(value) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"type", "id"}:
        raise ArchiveError("BACKUP_REFERENCE_INVALID")
    kind, identifier = value["type"], value["id"]
    if not isinstance(kind, str) or kind not in LOGICAL_TYPES or not isinstance(identifier, str) or not identifier or len(identifier) > 200:
        raise ArchiveError("BACKUP_REFERENCE_INVALID")
    return kind, identifier


def pointer_parts(value: str) -> list[str]:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 2000 or re.search(r"~(?![01])", value):
        raise ArchiveError("BACKUP_REFERENCE_INVALID")
    return [part.replace("~1", "/").replace("~0", "~") for part in value[1:].split("/")]


def pointer_get(document, pointer: str):
    value = document
    try:
        for part in pointer_parts(pointer):
            if isinstance(value, list):
                if not part.isdecimal() or str(int(part)) != part:
                    raise ArchiveError("BACKUP_REFERENCE_INVALID")
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise ArchiveError("BACKUP_REFERENCE_INVALID")
        return value
    except (KeyError, IndexError, ValueError) as error:
        raise ArchiveError("BACKUP_REFERENCE_INVALID") from error


def pointer_set(document, pointer: str, value):
    # Require an existing canonical location; callers cannot create a new field
    # or use Python's negative list indexing while remapping an untrusted file.
    pointer_get(document, pointer)
    parts = pointer_parts(pointer)
    parent = document
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    if isinstance(parent, list):
        parent[int(parts[-1])] = value
    else:
        parent[parts[-1]] = value


def validate_manifest(value, limits: ArchiveLimits = ArchiveLimits()) -> dict:
    expected = {"schemaVersion", "appVersion", "exportedAt", "objectType", "roots", "files", "objects", "references", "externalSources"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ArchiveError("BACKUP_MANIFEST_INVALID")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != SCHEMA_VERSION:
        raise ArchiveError("BACKUP_SCHEMA_UNSUPPORTED")
    if not isinstance(value["appVersion"], str) or not 1 <= len(value["appVersion"]) <= 100 or not isinstance(value["objectType"], str) or value["objectType"] not in {"story", "world"}:
        raise ArchiveError("BACKUP_MANIFEST_INVALID")
    try:
        exported = datetime.fromisoformat(value["exportedAt"].replace("Z", "+00:00"))
        if exported.tzinfo is None:
            raise ValueError()
    except (AttributeError, TypeError, ValueError) as error:
        raise ArchiveError("BACKUP_MANIFEST_INVALID") from error
    if any(not isinstance(value[name], list) for name in ["roots", "files", "objects", "references", "externalSources"]):
        raise ArchiveError("BACKUP_MANIFEST_INVALID")
    if not value["files"] or len(value["files"]) + 1 > limits.max_entries or not value["objects"] or len(value["objects"]) > limits.max_objects or len(value["references"]) > limits.max_references:
        raise ArchiveError("BACKUP_ENTRY_LIMIT")
    paths, folded, total = {}, set(), 0
    for entry in value["files"]:
        if not isinstance(entry, dict) or set(entry) not in [{"path", "kind", "mediaType", "size", "sha256"}, {"path", "kind", "mediaType", "size", "sha256", "license"}]:
            raise ArchiveError("BACKUP_MANIFEST_INVALID")
        path = safe_path(entry["path"])
        if path == "manifest.json" or path.casefold() in folded:
            raise ArchiveError("BACKUP_PATH_DUPLICATE")
        folded.add(path.casefold()); kind = entry["kind"]
        if not isinstance(kind, str) or kind not in MEDIA or path.split("/")[0] != {"data": "data", "body": "body", "asset": "assets"}[kind] or MEDIA[kind].get(PurePosixPath(path).suffix.lower()) != entry["mediaType"]:
            raise ArchiveError("BACKUP_FILE_TYPE_INVALID")
        size = entry["size"]
        if type(size) is not int or size < 0 or not isinstance(entry["sha256"], str) or re.fullmatch("[0-9a-f]{64}", entry["sha256"]) is None:
            raise ArchiveError("BACKUP_MANIFEST_INVALID")
        if kind == "asset" and (size > limits.max_image_bytes or not isinstance(entry.get("license"), str) or not 1 <= len(entry["license"]) <= 2000):
            raise ArchiveError("BACKUP_ASSET_INVALID")
        if kind == "data" and size > limits.max_json_bytes:
            raise ArchiveError("BACKUP_JSON_SIZE_LIMIT")
        total += size; paths[path] = entry
    if total > limits.max_uncompressed_bytes:
        raise ArchiveError("BACKUP_SIZE_LIMIT")
    objects, locations = {}, set()
    for item in value["objects"]:
        if not isinstance(item, dict) or set(item) - {"type", "id", "path", "idPointer", "ownerPointer"} or not {"type", "id", "path"} <= set(item):
            raise ArchiveError("BACKUP_MANIFEST_INVALID")
        key = object_key({"type": item["type"], "id": item["id"]})
        path = safe_path(item["path"]); location = (path, item.get("idPointer", "/id"))
        pointer_parts(location[1])
        if key in objects or location in locations or path not in paths or paths[path]["kind"] != "data":
            raise ArchiveError("BACKUP_OBJECT_INVALID")
        if item.get("ownerPointer") is not None:
            pointer_parts(item["ownerPointer"])
        objects[key] = item; locations.add(location)
    if not value["roots"] or any(object_key(root) not in objects for root in value["roots"]) or not any(root["type"] == value["objectType"] for root in value["roots"]):
        raise ArchiveError("BACKUP_ROOT_INVALID")
    reference_locations = {}
    for reference in value["references"]:
        if not isinstance(reference, dict) or set(reference) != {"from", "to", "field", "required"} or type(reference["required"]) is not bool:
            raise ArchiveError("BACKUP_REFERENCE_INVALID")
        origin, target = object_key(reference["from"]), object_key(reference["to"])
        pointer_parts(reference["field"])
        if origin not in objects or target not in objects:
            raise ArchiveError("BACKUP_REFERENCE_MISSING")
        location = (objects[origin]["path"], reference["field"])
        if location in locations or (location in reference_locations and reference_locations[location] != target):
            raise ArchiveError("BACKUP_REFERENCE_INVALID")
        reference_locations[location] = target
    source_locations = set()
    for source in value["externalSources"]:
        if not isinstance(source, dict) or set(source) != {"from", "field", "label", "timepoint"} or object_key(source["from"]) not in objects or not isinstance(source["label"], str) or not 1 <= len(source["label"]) <= 500:
            raise ArchiveError("BACKUP_EXTERNAL_SOURCE_INVALID")
        pointer_parts(source["field"])
        location = (objects[object_key(source["from"])]["path"], source["field"])
        if location in locations or location in reference_locations or location in source_locations:
            raise ArchiveError("BACKUP_EXTERNAL_SOURCE_INVALID")
        source_locations.add(location)
        if len(encode_json(source["timepoint"])) > 8192:
            raise ArchiveError("BACKUP_EXTERNAL_SOURCE_INVALID")
    # All replacement locations must be disjoint. Replacing an ancestor object
    # before its descendant ID/reference would make remapping order dependent.
    writes = {}
    for item in objects.values():
        writes[(item["path"], item.get("idPointer", "/id"))] = ("id", (item["type"], item["id"]))
        if item.get("ownerPointer") is not None:
            location = (item["path"], item["ownerPointer"])
            if location in writes and writes[location] != ("owner", None):
                raise ArchiveError("BACKUP_REFERENCE_INVALID")
            writes[location] = ("owner", None)
    for location, target in reference_locations.items():
        if location in writes:
            raise ArchiveError("BACKUP_REFERENCE_INVALID")
        writes[location] = ("ref", target)
    for location in source_locations:
        if location in writes:
            raise ArchiveError("BACKUP_REFERENCE_INVALID")
        writes[location] = ("detached", None)
    # Sorting decoded path segments makes all descendants adjacent. The depth
    # bound avoids quadratic scans for the 500k-reference permitted package.
    by_file = {}
    for path, pointer in writes:
        by_file.setdefault(path, []).append(tuple(pointer_parts(pointer)))
    for pointers in by_file.values():
        previous = None
        for pointer in sorted(pointers):
            if previous is not None and pointer[:len(previous)] == previous:
                raise ArchiveError("BACKUP_REFERENCE_INVALID")
            previous = pointer
    encoded_size = len(encode_json(value))
    if encoded_size > limits.max_manifest_bytes:
        raise ArchiveError("BACKUP_MANIFEST_SIZE_LIMIT")
    if total + encoded_size > limits.max_uncompressed_bytes:
        raise ArchiveError("BACKUP_SIZE_LIMIT")
    return copy.deepcopy(value)


def validate_documents(manifest: dict, documents: dict) -> None:
    objects = {(item["type"], item["id"]): item for item in manifest["objects"]}
    for item in objects.values():
        if pointer_get(documents[item["path"]], item.get("idPointer", "/id")) != item["id"]:
            raise ArchiveError("BACKUP_OBJECT_ID_MISMATCH")
        if item.get("ownerPointer") is not None and not isinstance(pointer_get(documents[item["path"]], item["ownerPointer"]), str):
            raise ArchiveError("BACKUP_OWNER_INVALID")
    for reference in manifest["references"]:
        origin = objects[object_key(reference["from"])]
        if pointer_get(documents[origin["path"]], reference["field"]) != reference["to"]["id"]:
            raise ArchiveError("BACKUP_REFERENCE_MISMATCH")
    for source in manifest["externalSources"]:
        origin = objects[object_key(source["from"])]
        if pointer_get(documents[origin["path"]], source["field"]) is not None:
            raise ArchiveError("BACKUP_EXTERNAL_SOURCE_LIVE_LINK")
