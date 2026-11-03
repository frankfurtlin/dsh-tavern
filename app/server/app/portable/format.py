"""Versioned .srbackup framing with standard streaming AES-256-GCM and scrypt."""
from dataclasses import dataclass
import os
import struct
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import ArchiveError

MAGIC = b"SRBKUP01"
FORMAT_VERSION = 1
KDF_N, KDF_R, KDF_P = 32768, 8, 1
HEADER = struct.Struct(">8sHIII16s12sQ")
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
MAX_CIPHERTEXT_BYTES = 2 * 1024**3 + 64 * 1024**2


@dataclass(frozen=True)
class BackupHeader:
    version: int
    n: int
    r: int
    p: int
    salt: bytes
    nonce: bytes
    ciphertext_length: int

    def encode(self) -> bytes:
        return HEADER.pack(MAGIC, self.version, self.n, self.r, self.p, self.salt, self.nonce, self.ciphertext_length)


def _key(password: str, header: BackupHeader) -> bytes:
    if not isinstance(password, str) or not password:
        raise ArchiveError("BACKUP_PASSWORD_REQUIRED")
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ArchiveError("BACKUP_PASSWORD_REQUIRED") from error
    if len(encoded) > 1024:
        raise ArchiveError("BACKUP_PASSWORD_REQUIRED")
    # Header policy is checked before allocating KDF memory; hostile N/r/p cannot
    # turn a password attempt into unbounded memory or CPU work.
    if (header.n, header.r, header.p) != (KDF_N, KDF_R, KDF_P):
        raise ArchiveError("BACKUP_KDF_UNSUPPORTED")
    return Scrypt(salt=header.salt, length=32, n=header.n, r=header.r, p=header.p).derive(encoded)


def _length(stream: BinaryIO) -> int:
    try:
        position = stream.tell(); stream.seek(0, os.SEEK_END); length = stream.tell(); stream.seek(position)
        return length
    except (OSError, AttributeError) as error:
        raise ArchiveError("BACKUP_STREAM_NOT_SEEKABLE") from error


def inspect_header(stream: BinaryIO) -> BackupHeader:
    stream.seek(0)
    raw = stream.read(HEADER.size)
    if len(raw) != HEADER.size:
        raise ArchiveError("BACKUP_HEADER_INVALID")
    magic, version, n, r, p, salt, nonce, length = HEADER.unpack(raw)
    if magic != MAGIC:
        raise ArchiveError("BACKUP_FORMAT_INVALID")
    if version != FORMAT_VERSION:
        raise ArchiveError("BACKUP_VERSION_UNSUPPORTED")
    if (n, r, p) != (KDF_N, KDF_R, KDF_P):
        raise ArchiveError("BACKUP_KDF_UNSUPPORTED")
    if length < TAG_BYTES or length > MAX_CIPHERTEXT_BYTES:
        raise ArchiveError("BACKUP_SIZE_LIMIT")
    if _length(stream) != HEADER.size + length:
        raise ArchiveError("BACKUP_LENGTH_INVALID")
    return BackupHeader(version, n, r, p, salt, nonce, length)


def encrypt_payload(plain: BinaryIO, output: BinaryIO, password: str) -> BackupHeader:
    """Encrypt a seekable ZIP stream without loading it into memory or Base64."""
    size = _length(plain)
    if size + TAG_BYTES > MAX_CIPHERTEXT_BYTES:
        raise ArchiveError("BACKUP_SIZE_LIMIT")
    header = BackupHeader(FORMAT_VERSION, KDF_N, KDF_R, KDF_P, os.urandom(16), os.urandom(12), size + TAG_BYTES)
    raw = header.encode(); encryptor = Cipher(algorithms.AES(_key(password, header)), modes.GCM(header.nonce)).encryptor()
    encryptor.authenticate_additional_data(raw)
    plain.seek(0); output.write(raw)
    received = 0
    while data := plain.read(CHUNK_BYTES):
        received += len(data)
        if received > size:
            raise ArchiveError("BACKUP_SOURCE_CHANGED")
        output.write(encryptor.update(data))
    if received != size:
        raise ArchiveError("BACKUP_SOURCE_CHANGED")
    output.write(encryptor.finalize()); output.write(encryptor.tag)
    return header


def decrypt_payload(source: BinaryIO, private_output: BinaryIO, password: str) -> BackupHeader:
    """Write only to private staging; caller must not publish it before return.

    A failed tag may have produced unauthenticated staging bytes, which the
    owning staging context must remove. No database or domain file is touched.
    """
    header = inspect_header(source)
    source.seek(HEADER.size + header.ciphertext_length - TAG_BYTES)
    tag = source.read(TAG_BYTES)
    decryptor = Cipher(algorithms.AES(_key(password, header)), modes.GCM(header.nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(header.encode())
    source.seek(HEADER.size)
    remaining = header.ciphertext_length - TAG_BYTES
    try:
        while remaining:
            data = source.read(min(CHUNK_BYTES, remaining))
            if not data:
                raise ArchiveError("BACKUP_LENGTH_INVALID")
            remaining -= len(data); private_output.write(decryptor.update(data))
        private_output.write(decryptor.finalize())
    except InvalidTag as error:
        raise ArchiveError("BACKUP_PASSWORD_OR_INTEGRITY_INVALID") from error
    return header
