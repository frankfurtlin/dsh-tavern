"""Bounded ZIP inspection and owned staging, without extractall or database IO."""
import codecs
from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import stat
import tempfile
from typing import BinaryIO
import zipfile

from PIL import Image, UnidentifiedImageError

from .errors import ArchiveError
from .format import CHUNK_BYTES, decrypt_payload, encrypt_payload
from .manifest import ArchiveLimits, encode_json, parse_json, safe_path, validate_documents, validate_manifest
from .remap import validate_private_policy


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, relative: str) -> Path:
    path = root / safe_path(relative)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ArchiveError("BACKUP_PATH_INVALID")
    return path


def _asset(path: Path, media: str):
    expected = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[media]
    try:
        with Image.open(path) as image:
            # Pillow PNG.verify must run immediately after open. getexif and
            # frame inspection may load/close its stream, so use a second open
            # for metadata and decode checks rather than rejecting valid PNGs.
            image.verify()
        with Image.open(path) as image:
            if image.format != expected or not 1 <= image.width <= 8192 or not 1 <= image.height <= 8192 or getattr(image, "n_frames", 1) != 1:
                raise ArchiveError("BACKUP_ASSET_INVALID")
            if image.getexif().get(34853):
                raise ArchiveError("BACKUP_ASSET_LOCATION_METADATA")
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, RuntimeError, SyntaxError, Image.DecompressionBombError) as error:
        raise ArchiveError("BACKUP_ASSET_INVALID") from error


def _text(path: Path):
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        with path.open("rb") as source:
            while chunk := source.read(CHUNK_BYTES):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise ArchiveError("BACKUP_TEXT_INVALID") from error


def validate_package_files(root: Path, manifest: dict, limits: ArchiveLimits) -> dict:
    documents = {}
    for entry in manifest["files"]:
        path = _inside(root, entry["path"])
        if not path.is_file() or path.stat().st_size != entry["size"] or _hash_file(path) != entry["sha256"]:
            raise ArchiveError("BACKUP_FILE_HASH_MISMATCH")
        if entry["kind"] == "data":
            documents[entry["path"]] = parse_json(path.read_bytes(), limits.max_json_bytes)
        elif entry["kind"] == "asset":
            _asset(path, entry["mediaType"])
        else:
            _text(path)
    validate_documents(manifest, documents)
    validate_private_policy(manifest, documents)
    return documents


def write_backup(package_root: Path, manifest: dict, output: BinaryIO, password: str,
                 work_root: Path, limits: ArchiveLimits = ArchiveLimits()) -> dict:
    """Caller supplies an authorized, stable snapshot in its own package root."""
    manifest = validate_manifest(manifest, limits)
    validate_package_files(package_root, manifest, limits)
    work_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="reader-bk-export-", dir=work_root))
    try:
        payload = temporary / "payload.zip"
        with zipfile.ZipFile(payload, "w", allowZip64=True) as archive:
            info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o600) << 16; info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, encode_json(manifest))
            for entry in manifest["files"]:
                path = _inside(package_root, entry["path"])
                info = zipfile.ZipInfo(entry["path"], date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = (stat.S_IFREG | 0o600) << 16; info.file_size = entry["size"]
                info.compress_type = zipfile.ZIP_STORED if entry["kind"] == "asset" else zipfile.ZIP_DEFLATED
                digest, written = hashlib.sha256(), 0
                with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                    while chunk := source.read(CHUNK_BYTES):
                        digest.update(chunk); written += len(chunk); target.write(chunk)
                if written != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise ArchiveError("BACKUP_SOURCE_CHANGED")
        with payload.open("rb") as plain:
            header = encrypt_payload(plain, output, password)
    finally:
        _remove_owned_stage(temporary, work_root, "reader-bk-export-")
    return {"formatVersion": header.version, "schemaVersion": manifest["schemaVersion"], "files": len(manifest["files"]), "objects": len(manifest["objects"])}


def _remove_owned_stage(root: Path, parent: Path, prefix: str):
    # Resolve and check the actual allocated absolute target before recursive
    # deletion, including on Windows. Caller uploads/package roots are excluded.
    if root.is_symlink() or root.resolve().parent != parent.resolve() or not root.name.startswith(prefix):
        raise ArchiveError("BACKUP_STAGING_CLEANUP_UNSAFE")
    if root.exists():
        shutil.rmtree(root)


@dataclass
class PreparedArchive:
    root: Path
    parent: Path
    files_root: Path
    manifest: dict
    documents: dict
    limits: ArchiveLimits = ArchiveLimits()

    @property
    def object_counts(self) -> dict:
        counts = {}
        for item in self.manifest["objects"]:
            counts[item["type"]] = counts.get(item["type"], 0) + 1
        return counts

    def close(self):
        # This path was allocated by us; never recursively remove a caller's
        # original upload, story root, or a computed unchecked target.
        _remove_owned_stage(self.root, self.parent, "reader-bk-import-")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def prepare_backup(source: BinaryIO, password: str, staging_root: Path,
                   limits: ArchiveLimits = ArchiveLimits(), *, allocated_root: Path | None = None) -> PreparedArchive:
    """Authenticate and verify everything before returning a confirmable stage."""
    staging_root.mkdir(parents=True, exist_ok=True)
    root = allocated_root or Path(tempfile.mkdtemp(prefix="reader-bk-import-", dir=staging_root))
    if root.is_symlink() or root.resolve().parent!=staging_root.resolve() or not root.name.startswith("reader-bk-import-") or not root.is_dir() or any(root.iterdir()):
        raise ArchiveError("BACKUP_STAGING_INVALID")
    prepared = PreparedArchive(root, staging_root, root / "files", {}, {}, limits)
    try:
        payload = root / "payload.zip"
        with payload.open("wb") as plain:
            decrypt_payload(source, plain, password)
        prepared.files_root.mkdir()
        with zipfile.ZipFile(payload, "r") as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_entries or not entries:
                raise ArchiveError("BACKUP_ENTRY_LIMIT")
            seen, total, by_path = set(), 0, {}
            for entry in entries:
                if entry.orig_filename != entry.filename:
                    raise ArchiveError("BACKUP_PATH_INVALID")
                path = safe_path(entry.filename)
                mode = (entry.external_attr >> 16) & 0xFFFF
                if entry.is_dir() or mode & 0o111 or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}) or entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise ArchiveError("BACKUP_ZIP_ENTRY_INVALID")
                if path.casefold() in seen:
                    raise ArchiveError("BACKUP_PATH_DUPLICATE")
                seen.add(path.casefold()); by_path[path] = entry; total += entry.file_size
                if total > limits.max_uncompressed_bytes:
                    raise ArchiveError("BACKUP_SIZE_LIMIT")
            if "manifest.json" not in by_path or by_path["manifest.json"].file_size > limits.max_manifest_bytes:
                raise ArchiveError("BACKUP_MANIFEST_INVALID")
            with archive.open(by_path["manifest.json"]) as data:
                manifest = validate_manifest(parse_json(data.read(limits.max_manifest_bytes + 1), limits.max_manifest_bytes), limits)
            expected = {entry["path"]: entry for entry in manifest["files"]}
            if set(by_path) != set(expected) | {"manifest.json"}:
                raise ArchiveError("BACKUP_INVENTORY_MISMATCH")
            for name, description in expected.items():
                entry = by_path[name]
                if entry.file_size != description["size"]:
                    raise ArchiveError("BACKUP_FILE_HASH_MISMATCH")
                target = _inside(prepared.files_root, name); target.parent.mkdir(parents=True, exist_ok=True)
                received, digest = 0, hashlib.sha256()
                with archive.open(entry) as data, target.open("xb") as output:
                    while chunk := data.read(CHUNK_BYTES):
                        received += len(chunk)
                        if received > description["size"]:
                            raise ArchiveError("BACKUP_SIZE_LIMIT")
                        digest.update(chunk); output.write(chunk)
                if received != description["size"] or digest.hexdigest() != description["sha256"]:
                    raise ArchiveError("BACKUP_FILE_HASH_MISMATCH")
        prepared.manifest = manifest
        prepared.documents = validate_package_files(prepared.files_root, manifest, limits)
        # Plaintext ZIP is no longer needed; original ciphertext remains intact.
        payload.unlink()
        return prepared
    except (zipfile.BadZipFile, EOFError, RuntimeError, OSError) as error:
        prepared.close()
        raise ArchiveError("BACKUP_ZIP_INVALID") from error
    except BaseException:
        prepared.close()
        raise
