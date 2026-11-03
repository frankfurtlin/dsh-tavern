"""Structured safe inline formatting and bounded UTF-8 prose file ranges."""
import hashlib
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .errors import ArchiveError
from .manifest import safe_path

INLINE_TAGS = frozenset({"p", "div", "body", "em", "strong", "b", "i", "br", "span", "blockquote", "li", "h1", "h2", "h3", "h4", "img"})
PACK_BYTES = 16 * 1024**2
MAX_SINGLE_TEXT = 64 * 1024**2


def _comment(value: str):
    if "--" in value or value.endswith("-"):
        raise ArchiveError("BACKUP_INLINE_INVALID")
    return value


def encode_inline(markup: str | None):
    """Never archive executable/user HTML. Preserve only the reader's grammar.

    Images reference owned asset IDs, not URLs. Import reconstructs markup from
    these typed nodes; it never feeds an archive-provided HTML string to Vue.
    """
    if markup is None:
        return None
    if not isinstance(markup, str):
        raise ArchiveError("BACKUP_INLINE_INVALID")
    try:
        if len(markup.encode("utf-8")) > MAX_SINGLE_TEXT:
            raise ArchiveError("BACKUP_INLINE_INVALID")
    except UnicodeEncodeError as error:
        raise ArchiveError("BACKUP_INLINE_INVALID") from error
    soup = BeautifulSoup(markup, "html.parser")
    count = 0
    def node(item, depth=0):
        nonlocal count
        count += 1
        if depth > 128 or count > 200000:
            raise ArchiveError("BACKUP_INLINE_LIMIT")
        if isinstance(item, Comment):
            return {"kind": "comment", "value": _comment(str(item))}
        if isinstance(item, NavigableString):
            return {"kind": "text", "value": str(item)}
        if not isinstance(item, Tag) or item.name not in INLINE_TAGS:
            raise ArchiveError("BACKUP_INLINE_INVALID")
        attributes = {}
        if item.name == "img":
            if set(item.attrs) - {"data-asset", "alt"} or not isinstance(item.get("data-asset"), str) or not item["data-asset"]:
                raise ArchiveError("BACKUP_INLINE_INVALID")
            attributes = {"assetId": item["data-asset"], "alt": item.get("alt", "插图")}
            if not isinstance(attributes["alt"], str) or len(attributes["alt"]) > 1000:
                raise ArchiveError("BACKUP_INLINE_INVALID")
        elif item.attrs:
            raise ArchiveError("BACKUP_INLINE_INVALID")
        return {"kind": "tag", "tag": item.name, "attributes": attributes, "children": [node(child, depth + 1) for child in item.children]}
    return [node(item) for item in soup.contents]


def decode_inline(nodes) -> str | None:
    if nodes is None:
        return None
    if not isinstance(nodes, list):
        raise ArchiveError("BACKUP_INLINE_INVALID")
    soup = BeautifulSoup("", "html.parser")
    count = 0
    def node(value, depth=0):
        nonlocal count
        count += 1
        if not isinstance(value, dict) or depth > 128 or count > 200000:
            raise ArchiveError("BACKUP_INLINE_LIMIT")
        if not isinstance(value.get("kind"), str):
            raise ArchiveError("BACKUP_INLINE_INVALID")
        if value.get("kind") in {"text", "comment"}:
            if set(value) != {"kind", "value"} or not isinstance(value["value"], str):
                raise ArchiveError("BACKUP_INLINE_INVALID")
            return Comment(_comment(value["value"])) if value["kind"] == "comment" else NavigableString(value["value"])
        if set(value) != {"kind", "tag", "attributes", "children"} or value["kind"] != "tag" or not isinstance(value["tag"], str) or value["tag"] not in INLINE_TAGS or not isinstance(value["attributes"], dict) or not isinstance(value["children"], list):
            raise ArchiveError("BACKUP_INLINE_INVALID")
        tag = soup.new_tag(value["tag"])
        attributes = value["attributes"]
        if value["tag"] == "img":
            if set(attributes) != {"assetId", "alt"} or not isinstance(attributes["assetId"], str) or not attributes["assetId"] or not isinstance(attributes["alt"], str) or len(attributes["alt"]) > 1000:
                raise ArchiveError("BACKUP_INLINE_INVALID")
            tag["data-asset"] = attributes["assetId"]; tag["alt"] = attributes["alt"]
        elif attributes:
            raise ArchiveError("BACKUP_INLINE_INVALID")
        if value["tag"] in {"img", "br"} and value["children"]:
            raise ArchiveError("BACKUP_INLINE_INVALID")
        for child in value["children"]:
            tag.append(node(child, depth + 1))
        return tag
    for value in nodes:
        soup.append(node(value))
    output = str(soup)
    try:
        if len(output.encode("utf-8")) > MAX_SINGLE_TEXT:
            raise ArchiveError("BACKUP_INLINE_LIMIT")
    except UnicodeEncodeError as error:
        raise ArchiveError("BACKUP_INLINE_INVALID") from error
    return output


class TextPacks:
    """Pack paragraphs without one ZIP entry per paragraph or full-book Base64."""
    def __init__(self, root: Path):
        self.root = root
        self.entries = []
        self.handle = None
        self.size = 0
        self.digest = None
        self.path = None

    def _finish(self):
        if self.handle is not None:
            self.handle.flush(); self.handle.close(); self.handle = None
            self.entries.append({"path": self.path, "kind": "body", "mediaType": "text/plain", "size": self.size, "sha256": self.digest.hexdigest()})

    def write(self, value: str) -> dict:
        if not isinstance(value, str):
            raise ArchiveError("BACKUP_TEXT_INVALID")
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ArchiveError("BACKUP_TEXT_INVALID") from error
        if len(raw) > MAX_SINGLE_TEXT:
            raise ArchiveError("BACKUP_TEXT_SIZE_LIMIT")
        if self.handle is None or self.size + len(raw) > PACK_BYTES:
            self._finish(); self.path = f"body/pack-{len(self.entries):06d}.txt"
            target = self.root / self.path; target.parent.mkdir(parents=True, exist_ok=True)
            self.handle = target.open("xb"); self.size = 0; self.digest = hashlib.sha256()
        frame = {"path": self.path, "offset": self.size, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        self.handle.write(raw); self.digest.update(raw); self.size += len(raw)
        return frame

    def close(self):
        self._finish()


def read_text_frame(root: Path, value: dict, declared: dict) -> str:
    if not isinstance(value, dict) or set(value) != {"path", "offset", "size", "sha256"}:
        raise ArchiveError("BACKUP_TEXT_REFERENCE_INVALID")
    path = safe_path(value["path"]); description = declared.get(path)
    offset, size = value["offset"], value["size"]
    if description is None or description["kind"] != "body" or type(offset) is not int or type(size) is not int or offset < 0 or size < 0 or size > MAX_SINGLE_TEXT or offset + size > description["size"]:
        raise ArchiveError("BACKUP_TEXT_REFERENCE_INVALID")
    target = root / path
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ArchiveError("BACKUP_PATH_INVALID")
    with target.open("rb") as handle:
        handle.seek(offset); raw = handle.read(size)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != value["sha256"]:
        raise ArchiveError("BACKUP_TEXT_HASH_MISMATCH")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArchiveError("BACKUP_TEXT_INVALID") from error
