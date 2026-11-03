"""Actual portable archive crypto, ZIP attacks and typed reference fixtures.

Pure tests: run separately from the database-destructive server conftest. No
providers, credentials, private books or database connection are involved.
"""
import copy
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import stat
import zipfile

from PIL import Image
import pytest

from app.portable.errors import ArchiveError
from app.portable.format import BackupHeader, CHUNK_BYTES, HEADER, decrypt_payload, encrypt_payload, inspect_header
from app.portable.manifest import ArchiveLimits, encode_json, parse_json, pointer_set, safe_path, validate_manifest
from app.portable.package import PreparedArchive, prepare_backup, write_backup
from app.portable.remap import build_restore_plan

PASSWORD = "synthetic-backup-passphrase"


def package(tmp_path):
    root = tmp_path / "original"
    nodes = [
        ("story", {"id": "story-old", "title": "备份故事", "branchId": "branch-old"}),
        ("branch", {"id": "branch-old", "parentId": None, "bookId": "book-old"}),
        ("book", {"id": "book-old", "textPath": "body/chapter.txt"}),
        ("character_identity", {"id": "npc-one", "name": "阿远"}),
        ("character_identity", {"id": "npc-two", "name": "阿远"}),
        ("world", {"id": "world-old", "checkpointId": "cp-next", "sourceBookId": None, "sourceState": {"frozenText": "冻结来源"}}),
        ("checkpoint", {"id": "cp-old", "worldId": "world-old", "parentId": None, "state": {"entities": [{"id": "npc-one", "knowledge": ["隐秘记忆"]}], "inventory": []}}),
        ("checkpoint", {"id": "cp-next", "worldId": "world-old", "parentId": "cp-old", "state": {"entities": [{"id": "npc-two"}], "inventory": ["信件"]}}),
        ("task", {"id": "task-old", "status": "pending", "payload": {"storyId": "story-old"}}),
        ("historical_usage", {"id": "usage-old", "inputTokens": 153, "cost": None}),
        ("historical_profile", {"id": "profile-old", "model": "synthetic-offline-model"}),
    ]
    document = {"ownerId": "source-owner", "nodes": [value for _, value in nodes]}
    objects = [{"type": kind, "id": value["id"], "path": "data/objects.json", "idPointer": f"/nodes/{index}/id", "ownerPointer": "/ownerId"}
               for index, (kind, value) in enumerate(nodes)]
    references = []
    def ref(index, field, to_type, identifier):
        references.append({"from": {"type": nodes[index][0], "id": nodes[index][1]["id"]},
                           "to": {"type": to_type, "id": identifier}, "field": f"/nodes/{index}/{field}", "required": True})
    ref(0, "branchId", "branch", "branch-old"); ref(1, "bookId", "book", "book-old")
    ref(5, "checkpointId", "checkpoint", "cp-next")
    for index in [6, 7]:
        ref(index, "worldId", "world", "world-old")
        ref(index, "state/entities/0/id", "character_identity", "npc-one" if index == 6 else "npc-two")
    ref(7, "parentId", "checkpoint", "cp-old"); ref(8, "payload/storyId", "story", "story-old")
    png = io.BytesIO(); Image.new("RGB", (8, 8), "#186d61").save(png, "PNG")
    data = {"data/objects.json": encode_json(document), "body/chapter.txt": "第一章\n渡口😀。\n另一条线。".encode(), "assets/portrait.png": png.getvalue()}
    files = []
    for path, raw in data.items():
        target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        kind = {"data": "data", "body": "body", "assets": "asset"}[path.split("/")[0]]
        descriptor = {"path": path, "kind": kind, "mediaType": {"data": "application/json", "body": "text/plain", "asset": "image/png"}[kind], "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if kind == "asset": descriptor["license"] = "Original synthetic test illustration"
        files.append(descriptor)
    manifest = {"schemaVersion": 1, "appVersion": "next-test", "exportedAt": "2026-10-05T00:00:00+08:00", "objectType": "story",
                "roots": [{"type": "story", "id": "story-old"}], "files": files, "objects": objects, "references": references,
                "externalSources": [{"from": {"type": "world", "id": "world-old"}, "field": "/nodes/5/sourceBookId", "label": "未附带的来源小说", "timepoint": {"chapter": 3}}]}
    return root, manifest, data, document


def encrypted_zip(manifest, data, extra=None, mode=stat.S_IFREG | 0o600, compression=zipfile.ZIP_DEFLATED):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("manifest.json", encode_json(manifest))
        for path, raw in data.items():
            entry = zipfile.ZipInfo(path); entry.external_attr = mode << 16; entry.compress_type = compression
            archive.writestr(entry, raw)
        for path, raw in extra or []:
            archive.writestr(path, raw)
    output = io.BytesIO(); encrypt_payload(payload, output, PASSWORD); output.seek(0)
    return output


def assert_clean_failure(cipher, tmp_path, code, password=PASSWORD, limits=ArchiveLimits()):
    stage = tmp_path / "stage"
    original = cipher.getvalue()
    with pytest.raises(ArchiveError) as caught:
        prepare_backup(cipher, password, stage, limits)
    assert caught.value.code == code
    assert cipher.getvalue() == original
    assert list(stage.iterdir()) == []


def test_actual_encrypted_roundtrip_new_copy_and_reference_closure(tmp_path):
    root, manifest, data, document = package(tmp_path)
    cipher = io.BytesIO()
    result = write_backup(root, manifest, cipher, PASSWORD, tmp_path / "export")
    assert result["objects"] == 11
    assert list((tmp_path / "export").iterdir()) == []
    outer = cipher.getvalue()
    assert b"source-owner" not in outer and "隐秘记忆".encode() not in outer
    assert b"story-old" not in outer and b"manifest.json" not in outer
    with prepare_backup(cipher, PASSWORD, tmp_path / "stage") as prepared:
        assert prepared.object_counts["character_identity"] == 2
        assert not (prepared.root / "payload.zip").exists()
        for path, raw in data.items(): assert (prepared.files_root / path).read_bytes() == raw
        plan = build_restore_plan(prepared.manifest, prepared.documents, "new-owner")
        restored = plan.documents["data/objects.json"]
        assert restored["ownerId"] == "new-owner"
        assert restored["nodes"][3]["name"] == restored["nodes"][4]["name"] == "阿远"
        assert restored["nodes"][3]["id"] != restored["nodes"][4]["id"]
        assert restored["nodes"][7]["parentId"] == restored["nodes"][6]["id"]
        assert restored["nodes"][6]["state"]["entities"][0]["id"] == restored["nodes"][3]["id"]
        assert restored["nodes"][5]["sourceBookId"] is None
        assert plan.detached_sources[0]["status"] == "source-not-imported"
        assert len(plan.historical_objects) == 3 and len(plan.active_objects) == 8
        assert not plan.replay_historical_tasks and not plan.transfer_billing_credit
        assert prepared.documents["data/objects.json"] == document
        second = build_restore_plan(prepared.manifest, prepared.documents, "new-owner")
        assert not set(plan.id_map.values()) & set(second.id_map.values())
    assert list((tmp_path / "stage").iterdir()) == []
    for path, raw in data.items(): assert (root / path).read_bytes() == raw


def test_random_salt_nonce_authenticated_header_and_bounded_streams():
    class Bounded(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= CHUNK_BYTES
            return super().read(size)
    raw = b"large chunk data" * 180000
    first, second = io.BytesIO(), io.BytesIO()
    one = encrypt_payload(Bounded(raw), first, PASSWORD); two = encrypt_payload(Bounded(raw), second, PASSWORD)
    assert one.salt != two.salt and one.nonce != two.nonce and first.getvalue() != second.getvalue()
    decoded = io.BytesIO(); decrypt_payload(Bounded(first.getvalue()), decoded, PASSWORD)
    assert decoded.getvalue() == raw
    damaged = bytearray(first.getvalue()); damaged[26] ^= 1
    with pytest.raises(ArchiveError, match="BACKUP_PASSWORD_OR_INTEGRITY_INVALID"):
        decrypt_payload(io.BytesIO(damaged), io.BytesIO(), PASSWORD)


@pytest.mark.parametrize("mutation,code", [
    ("wrong-password", "BACKUP_PASSWORD_OR_INTEGRITY_INVALID"), ("tag", "BACKUP_PASSWORD_OR_INTEGRITY_INVALID"),
    ("ciphertext", "BACKUP_PASSWORD_OR_INTEGRITY_INVALID"), ("trailing", "BACKUP_LENGTH_INVALID"),
    ("truncated", "BACKUP_LENGTH_INVALID"), ("magic", "BACKUP_FORMAT_INVALID"),
    ("version", "BACKUP_VERSION_UNSUPPORTED"), ("kdf", "BACKUP_KDF_UNSUPPORTED"),
])
def test_invalid_outer_envelope_preserves_original_and_removes_private_stage(tmp_path, mutation, code):
    _, manifest, data, _ = package(tmp_path); cipher = encrypted_zip(manifest, data)
    raw = bytearray(cipher.getvalue())
    if mutation == "tag": raw[-1] ^= 1
    elif mutation == "ciphertext": raw[HEADER.size + 10] ^= 1
    elif mutation == "trailing": raw.extend(b"extra")
    elif mutation == "truncated": raw.pop()
    elif mutation == "magic": raw[0] ^= 1
    elif mutation == "version": raw[9] = 2
    elif mutation == "kdf": raw[10:14] = (2**28).to_bytes(4, "big")
    assert_clean_failure(io.BytesIO(raw), tmp_path, code, "wrong" if mutation == "wrong-password" else PASSWORD)


@pytest.mark.parametrize("path", ["../escape.json", "/tmp/escape.json", "C:/escape.json", "data\\escape.json", "data//escape.json", "data/./escape.json", "data/x/../../escape.json", "data/CON.json", "data/note. ", "data/name?.json", "data/name\x00.json", "data/cafe\u0301.json"])
def test_untrusted_paths_rejected_before_zip_extraction(path):
    with pytest.raises(ArchiveError, match="BACKUP_PATH_INVALID"): safe_path(path)


@pytest.mark.parametrize("attack,code", [
    ("absolute", "BACKUP_PATH_INVALID"), ("traversal", "BACKUP_PATH_INVALID"),
    ("case-alias", "BACKUP_PATH_DUPLICATE"), ("symlink", "BACKUP_ZIP_ENTRY_INVALID"),
    ("executable", "BACKUP_ZIP_ENTRY_INVALID"), ("unsupported-compression", "BACKUP_ZIP_ENTRY_INVALID"),
    ("unlisted", "BACKUP_INVENTORY_MISMATCH"), ("missing", "BACKUP_INVENTORY_MISMATCH"),
    ("hash", "BACKUP_FILE_HASH_MISMATCH"), ("html", "BACKUP_FILE_TYPE_INVALID"),
    ("image", "BACKUP_ASSET_INVALID"), ("text", "BACKUP_TEXT_INVALID"),
])
def test_actual_encrypted_zip_attacks_fail_atomically(tmp_path, attack, code):
    _, manifest, data, _ = package(tmp_path); extra = []; mode = stat.S_IFREG | 0o600; compression = zipfile.ZIP_DEFLATED
    if attack == "absolute": extra = [("/tmp/escape.json", b"x")]
    elif attack == "traversal": extra = [("../escape.json", b"x")]
    elif attack == "case-alias": extra = [("DATA/OBJECTS.JSON", b"x")]
    elif attack == "symlink": mode = stat.S_IFLNK | 0o600
    elif attack == "executable": mode = stat.S_IFREG | 0o700
    elif attack == "unsupported-compression": compression = zipfile.ZIP_BZIP2
    elif attack == "unlisted": extra = [("data/unused.json", b"{}")]
    elif attack == "missing": data.pop("body/chapter.txt")
    elif attack == "hash": data["body/chapter.txt"] = b"wrong"
    elif attack == "html": manifest["files"][1].update(path="body/chapter.html", mediaType="text/html")
    elif attack in {"image", "text"}:
        index = 2 if attack == "image" else 1; path = manifest["files"][index]["path"]
        raw = b"not an image" if attack == "image" else b"invalid utf8 \xff"
        data[path] = raw; manifest["files"][index].update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    assert_clean_failure(encrypted_zip(manifest, data, extra, mode, compression), tmp_path, code)
    assert not (tmp_path / "escape.json").exists()


@pytest.mark.parametrize("limit,code", [("entries", "BACKUP_ENTRY_LIMIT"), ("decoded", "BACKUP_SIZE_LIMIT"), ("image", "BACKUP_ASSET_INVALID")])
def test_independent_backup_quotas(tmp_path, limit, code):
    _, manifest, data, _ = package(tmp_path)
    limits = {"entries": replace(ArchiveLimits(), max_entries=3), "decoded": replace(ArchiveLimits(), max_uncompressed_bytes=100), "image": replace(ArchiveLimits(), max_image_bytes=10)}[limit]
    assert_clean_failure(encrypted_zip(manifest, data), tmp_path, code, limits=limits)


@pytest.mark.parametrize("attack,code", [("missing-ref", "BACKUP_REFERENCE_MISSING"), ("wrong-ref", "BACKUP_REFERENCE_MISMATCH"), ("live-source", "BACKUP_EXTERNAL_SOURCE_LIVE_LINK"), ("owner-ref-overlap", "BACKUP_REFERENCE_INVALID"), ("ancestor-overlap", "BACKUP_REFERENCE_INVALID"), ("owner-id-overlap", "BACKUP_REFERENCE_INVALID")])
def test_typed_graph_validation_does_not_silently_drop_objects(tmp_path, attack, code):
    _, manifest, data, document = package(tmp_path)
    if attack == "missing-ref": manifest["references"][0]["to"]["id"] = "missing"
    elif attack == "wrong-ref": document["nodes"][0]["branchId"] = "someone-else"
    elif attack == "live-source": document["nodes"][5]["sourceBookId"] = "unrelated-same-name-book"
    elif attack == "owner-ref-overlap": manifest["references"][0]["field"] = "/ownerId"
    elif attack == "ancestor-overlap": manifest["references"][0]["field"] = "/nodes"
    elif attack == "owner-id-overlap": manifest["objects"][0]["ownerPointer"] = "/nodes/0/id"
    raw = encode_json(document); data["data/objects.json"] = raw
    manifest["files"][0].update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    assert_clean_failure(encrypted_zip(manifest, data), tmp_path, code)


@pytest.mark.parametrize("field", ["apiKey", "access_token", "refreshToken", "passwordHash", "rawJournal", "journalPath", "accountBudget", "remainingCredit"])
def test_operational_credentials_and_transferable_credit_are_excluded(tmp_path, field):
    _, manifest, _, document = package(tmp_path); document[field] = "synthetic-forbidden"
    with pytest.raises(ArchiveError, match="BACKUP_PRIVATE_POLICY_INVALID"):
        build_restore_plan(manifest, {"data/objects.json": document}, "new-owner")


def test_undeclared_nested_owner_and_id_factory_collision_are_rejected(tmp_path):
    _, manifest, _, document = package(tmp_path)
    document["nodes"][0]["user_id"] = "other-owner"
    with pytest.raises(ArchiveError, match="BACKUP_OWNER_UNDECLARED"):
        build_restore_plan(manifest, {"data/objects.json": document}, "new-owner")
    document["nodes"][0].pop("user_id")
    with pytest.raises(ArchiveError, match="BACKUP_NEW_ID_INVALID"):
        build_restore_plan(manifest, {"data/objects.json": document}, "new-owner", id_factory=lambda: "a" * 32)


def test_unsupported_schema_duplicate_json_and_canonical_array_pointer(tmp_path):
    _, manifest, _, _ = package(tmp_path); manifest["schemaVersion"] = 2
    with pytest.raises(ArchiveError, match="BACKUP_SCHEMA_UNSUPPORTED"): validate_manifest(manifest)
    with pytest.raises(ArchiveError, match="BACKUP_JSON_DUPLICATE_KEY"): parse_json(b'{"id":"one","id":"two"}', 100)
    with pytest.raises(ArchiveError, match="BACKUP_JSON_INVALID"): parse_json(b'{"value":NaN}', 100)
    original = {"items": ["a", "b"]}
    with pytest.raises(ArchiveError, match="BACKUP_REFERENCE_INVALID"): pointer_set(original, "/items/-1", "bad")
    assert original == {"items": ["a", "b"]}


def test_cleanup_guard_cannot_remove_original_upload_directory(tmp_path):
    original = tmp_path / "existing-story"; original.mkdir(); (original / "keep.txt").write_text("keep")
    unsafe = PreparedArchive(original, tmp_path, original, {}, {})
    with pytest.raises(ArchiveError, match="BACKUP_STAGING_CLEANUP_UNSAFE"): unsafe.close()
    assert (original / "keep.txt").read_text() == "keep"


def test_export_failure_preserves_existing_snapshot_and_cleans_only_owned_stage(tmp_path, monkeypatch):
    root, manifest, data, _ = package(tmp_path)
    def disk_full(*_): raise OSError("synthetic disk full")
    monkeypatch.setattr("app.portable.package.encrypt_payload", disk_full)
    with pytest.raises(OSError): write_backup(root, manifest, io.BytesIO(), PASSWORD, tmp_path / "export")
    assert list((tmp_path / "export").iterdir()) == []
    for path, raw in data.items(): assert (root / path).read_bytes() == raw
