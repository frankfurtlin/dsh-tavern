"""Finite public reading exports. Private world state never enters this projection."""
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import re
from typing import Literal
from uuid import UUID
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .. import models as m
from ..assets.models import AssetPlacement, AssetVariant, ImageAsset
from ..assets.service import visible_scope
from ..db import SessionLocal
from ..domain import anchor_order, digest, make_anchor, owned, utf16_len, utf16_slice, validate_anchor
from ..errors import Problem
from ..schemas import Strict
from ..storage import private_path
from ..world.models import WorldCheckpoint
from ..world.service import checked_checkpoint, require_line
from ..world.state import ancestry
from .domain_codec import encode_inline
from .errors import ArchiveError
from .models import PortableOperation
from .package import _remove_owned_stage
from .service import active, allocate_stage, create_operation, fail_operation, owned_operation, stage_path

PROTOCOL = "reader-export-v1"
XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
MAX_BODY = 2 * 1024**3
MAX_IMAGE = 10 * 1024**2
ID = r"^[a-f0-9]{32}$"


class ReaderScope(Strict):
    kind: Literal["book"]
    bookId: str = Field(pattern=ID)
    branchId: str = Field(pattern=ID)
    revisionId: str = Field(pattern=ID)


class WorldScope(Strict):
    kind: Literal["world"]
    worldId: str = Field(pattern=ID)
    lineId: str = Field(pattern=ID)
    checkpointId: str = Field(pattern=ID)


class ReadingExportInput(Strict):
    format: Literal["txt", "epub"]
    scope: ReaderScope | WorldScope = Field(discriminator="kind")
    range: Literal["observed", "selected"] = "observed"
    startChapter: int | None = Field(default=None, strict=True, ge=1, le=10000)
    endChapter: int | None = Field(default=None, strict=True, ge=1, le=10000)
    allowUnseen: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def coherent_range(self):
        if self.range == "observed":
            if self.startChapter is not None or self.endChapter is not None or self.allowUnseen:
                raise ValueError("已读导出不接受未来范围")
        elif self.startChapter is None or self.endChapter is None or self.endChapter < self.startChapter:
            raise ValueError("请明确选择起止章节")
        return self


@dataclass
class PublicChapter:
    title: str
    paragraphs: list


def range_error():
    raise Problem("EXPORT_RANGE_EMPTY", "这条线还没有可导出的已读正文，请选择章节范围", 409)


def source_scope(db, owner, data):
    scope = data.scope
    if scope.kind == "book":
        book = owned(db, m.Book, scope.bookId, owner)
        branch = owned(db, m.Branch, scope.branchId, owner)
        revision = owned(db, m.Revision, scope.revisionId, owner)
        if branch.book_id != book.id or revision.branch_id != branch.id:
            raise Problem("RESOURCE_NOT_FOUND", "导出故事线不存在或不可访问", 404)
        progress = db.scalar(select(m.Progress).where(m.Progress.owner_id == owner, m.Progress.branch_id == branch.id,
                                                     m.Progress.revision_id == revision.id))
        observed = (-1, -1, -1)
        boundary = None
        if progress:
            validate_anchor(db, progress.max_observed_anchor, owner)
            boundary = progress.max_observed_anchor
            observed = anchor_order(revision, boundary)
        chapters = revision.manifest
        selected_start, selected_end = selected_range(data, len(chapters), observed[0])
        # A selected whole chapter is future unless its last paragraph was fully
        # read. Merely reaching its title does not authorize the rest by default.
        if data.range == "selected" and not data.allowUnseen and selected_end == observed[0]:
            last = owned(db, m.ContentBlock, chapters[selected_end]["blocks"][-1], owner)
            if observed[1:] != (len(chapters[selected_end]["blocks"]) - 1, utf16_len(last.text)):
                raise Problem("EXPORT_UNSEEN_CONFIRMATION_REQUIRED", "所选范围包含尚未读到的正文，请明确确认", 409)
        target = scope.model_dump()
        if boundary and data.range == "observed":
            target["anchor"] = boundary
        elif data.range == "selected":
            last = owned(db, m.ContentBlock, chapters[selected_end]["blocks"][-1], owner)
            target["anchor"] = make_anchor(book.id, branch.id, revision.id, last, utf16_len(last.text), utf16_len(last.text))
        return book.title, branch.name, book.source_label, ("book", book, revision, chapters, observed, selected_start, selected_end, target)
    world, line = require_line(db, owner, scope.worldId, scope.lineId)
    cp = checked_checkpoint(db, owner, world.id, scope.checkpointId)
    head = checked_checkpoint(db, owner, world.id, line.head_checkpoint_id)
    head_path = ancestry(db, head, owner)
    if cp.id not in {row.id for row in head_path}:
        raise Problem("RESOURCE_NOT_FOUND", "导出时间点不属于这条世界线", 404)
    chapters = ancestry(db, cp, owner)
    saved = next((index for index, row in enumerate(head_path) if row.id == line.reading_position.get("checkpointId")), -1)
    seen = min(saved, len(chapters) - 1) if saved >= 0 else -1
    start, end = selected_range(data, len(chapters), seen)
    # World reading_position is checkpoint-granular; no private state, summary,
    # options, motives, memory or event ledger is read to construct the file.
    return world.name, line.name, "互动世界公开正文 · 来源：" + world.source_title, ("world", world, line, chapters, seen, start, end, scope.model_dump())


def selected_range(data, total, observed):
    if not total or total > 10000:
        range_error()
    if data.range == "observed":
        if observed < 0:
            range_error()
        return 0, observed
    start, end = data.startChapter - 1, data.endChapter - 1
    if end >= total:
        raise Problem("EXPORT_RANGE_INVALID", "所选章节不在当前正文快照", 422)
    if end > observed and not data.allowUnseen:
        raise Problem("EXPORT_UNSEEN_CONFIRMATION_REQUIRED", "所选范围包含尚未读到的正文，请明确确认", 409)
    return start, end


def public_chapters(db, owner, projection, data):
    kind, parent, current, chapters, boundary, start, end, target = projection
    if kind == "world":
        for cp in chapters[start:end + 1]:
            parts = [text.strip() for text in re.split(r"\n\s*\n", cp.text) if text.strip()]
            if parts:
                yield PublicChapter(cp.title, [{"id": cp.id, "index": str(i), "text": text, "nodes": None, "full": True,
                                               "source": "互动世界创作", "scope": {**target, "checkpointId": cp.id}} for i, text in enumerate(parts)])
        return
    for ci in range(start, end + 1):
        chapter = chapters[ci]
        paragraphs = []
        blocks = {}
        for offset in range(0, len(chapter["blocks"]), 500):
            rows = db.scalars(select(m.ContentBlock).where(m.ContentBlock.owner_id == owner,
                m.ContentBlock.id.in_(chapter["blocks"][offset:offset + 500])))
            blocks.update((row.id, row) for row in rows)
        for bi, block_id in enumerate(chapter["blocks"]):
            if data.range == "observed" and (ci, bi) > boundary[:2]:
                break
            block = blocks.get(block_id)
            if not block or block.book_id != parent.id or block.chapter_id != chapter["id"]:
                raise Problem("EXPORT_REFERENCE_INVALID", "正文引用不完整，未生成导出文件", 422)
            full = not (data.range == "observed" and (ci, bi) == boundary[:2] and boundary[2] < utf16_len(block.text))
            text = block.text if full else utf16_slice(block.text, 0, boundary[2])
            if not text:
                continue
            # A partial paragraph uses escaped plain text, preventing its later
            # formatting or image from leaking through a full HTML fragment.
            nodes = encode_inline(block.html) if full and block.html else None
            if nodes:
                from bs4 import BeautifulSoup
                from ..importer import normalize
                if normalize(BeautifulSoup(block.html, "html.parser").get_text("", strip=False)) != block.text:
                    raise Problem("EXPORT_INLINE_MISMATCH", "正文与排版内容不一致，原文已保留", 422)
            paragraphs.append({"id": block.id, "index": None, "text": text, "nodes": nodes, "full": full,
                               "source": "原文" if block.source == "original" else "故事创作", "scope": target})
        if paragraphs:
            yield PublicChapter(chapter["title"], paragraphs)


def xml_text(value):
    # XML 1.0 does not admit NUL or isolated surrogates. Fail with a public code
    # instead of emitting an EPUB that cannot be opened or silently changing prose.
    if any(ord(ch) < 32 and ch not in "\t\r\n" or 0xD800 <= ord(ch) <= 0xDFFF or ord(ch) in (0xFFFE, 0xFFFF) for ch in value):
        raise Problem("EXPORT_TEXT_INVALID", "正文包含不适合导出的字符，原文已保留", 422)
    return value


def xhtml(title):
    root = ET.Element("html", {"xmlns": XHTML, "xmlns:epub": EPUB, "lang": "zh-CN"})
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "meta", {"charset": "utf-8"})
    ET.SubElement(head, "title").text = xml_text(title)
    ET.SubElement(head, "link", {"rel": "stylesheet", "type": "text/css", "href": "reader.css"})
    return root, ET.SubElement(root, "body")


def xml_bytes(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class ExportImages:
    def __init__(self, db, owner, projection, root):
        self.db, self.owner, self.projection, self.root = db, owner, projection, root
        self.images = {}; self.total = 0
        self.source = db.get(m.SourceDocument, projection[1].source_id) if projection[0] == "book" else None
        if self.source and self.source.owner_id != owner:
            raise Problem("RESOURCE_NOT_FOUND", "图片来源不可访问", 404)
        root_field = "bookId" if projection[0] == "book" else "worldId"
        self.placed = {}
        for row in db.scalars(select(AssetPlacement).where(AssetPlacement.owner_id == owner,
            AssetPlacement.scope[root_field].astext == projection[1].id, AssetPlacement.purpose == "illustration")
            .order_by(AssetPlacement.order, AssetPlacement.id).limit(10001)):
            self.placed.setdefault((row.content_id, row.side), []).append(row)
        if sum(map(len, self.placed.values())) > 10000:
            raise Problem("EXPORT_IMAGE_LIMIT", "导出图片引用超出限额", 422)

    def add(self, key, raw, license_note, source_kind, alt):
        if key in self.images:
            return self.images[key]
        if not raw or len(raw) > MAX_IMAGE or len(self.images) >= 2000 or self.total + len(raw) > 512 * 1024**2:
            raise Problem("EXPORT_IMAGE_LIMIT", "导出图片超出限额，未生成文件", 422)
        # Decode sanitized, local bytes; no external image URL is ever requested.
        from ..assets.imaging import decode
        mime = "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/webp" if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" else "image/jpeg" if raw[:3] == b"\xff\xd8\xff" else None
        if mime is None:
            raise Problem("EXPORT_IMAGE_INVALID", "插图格式不适合导出，原图已保留", 422)
        image = decode(raw, mime, max_bytes=MAX_IMAGE)
        # Re-encode to remove EXIF and nonessential metadata from old EPUB imports.
        import io
        output = io.BytesIO(); image.save(output, format="PNG"); safe = output.getvalue()
        if len(safe) > MAX_IMAGE or self.total + len(safe) > 512 * 1024**2:
            raise Problem("EXPORT_IMAGE_LIMIT", "导出插图超出限额", 422)
        name = "images/image-" + str(len(self.images) + 1).zfill(4) + ".png"
        target = self.root / name; target.parent.mkdir(exist_ok=True); target.write_bytes(safe)
        row = {"href": name, "mediaType": "image/png", "sha256": digest(safe), "size": len(safe),
               "license": license_note, "source": source_kind, "alt": alt}
        self.images[key] = row; self.total += len(safe)
        return row

    def inline(self, identifier, alt):
        ref = self.source.source_map.get("assets", {}).get(identifier) if self.source else None
        if not isinstance(ref, str):
            raise Problem("EXPORT_IMAGE_MISSING", "正文插图已不可用，未生成不完整文件", 422)
        path = private_path(self.owner, ref)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_IMAGE:
            raise Problem("EXPORT_IMAGE_MISSING", "正文插图已不可用，未生成不完整文件", 422)
        return self.add("inline:" + identifier, path.read_bytes(), self.projection[1].source_label, "导入正文附图", alt)

    def placements(self, paragraph, side):
        result = []
        for p in self.placed.get((paragraph["id"], side), []):
            if p.paragraph_id is not None and p.paragraph_id != (paragraph["index"] if self.projection[0] == "world" else paragraph["id"]):
                continue
            if not paragraph["full"] and side == "after":
                continue
            if not visible_scope(self.db, p.scope, paragraph["scope"], self.owner):
                continue
            asset = owned(self.db, ImageAsset, p.asset_id, self.owner)
            variant = self.db.get(AssetVariant, (asset.id, "reading"))
            if not variant or digest(variant.data) != variant.sha256 or not asset.license_note.strip():
                raise Problem("EXPORT_IMAGE_MISSING", "插图字节或许可记录不可用", 422)
            result.append(self.add("asset:" + asset.id, variant.data, asset.license_note, asset.source_kind, p.alt or "故事插图"))
        return result


def append_image(parent, row):
    ET.SubElement(parent, "img", {"src": row["href"], "alt": xml_text(row["alt"])})


def append_nodes(parent, nodes, images):
    for node in nodes:
        if node["kind"] == "comment":
            continue
        if node["kind"] == "text":
            value = xml_text(node["value"])
            if len(parent):
                parent[-1].tail = (parent[-1].tail or "") + value
            else:
                parent.text = (parent.text or "") + value
            continue
        if node["tag"] == "img":
            append_image(parent, images.inline(node["attributes"]["assetId"], node["attributes"]["alt"]))
        elif node["tag"] == "body":
            append_nodes(parent, node["children"], images)
        else:
            child = ET.SubElement(parent, node["tag"])
            append_nodes(child, node["children"], images)


def write_reading(db, owner, data, root):
    title, line, source, projection = source_scope(db, owner, data)
    images = ExportImages(db, owner, projection, root)
    notes = []; chapter_count = 0; body_bytes = 0
    chapters = public_chapters(db, owner, projection, data)
    file = root / ("reading." + data.format)
    txt = file.open("x", encoding="utf-8", newline="\n") if data.format == "txt" else None
    book = ZipFile(file, "x", compression=ZIP_DEFLATED) if data.format == "epub" else None
    try:
        if txt:
            txt.write(title + "\n" + line + "\n\n")
        else:
            book.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
            container = ET.Element("container", {"version": "1.0", "xmlns": "urn:oasis:names:tc:opendocument:xmlns:container"})
            files = ET.SubElement(container, "rootfiles")
            ET.SubElement(files, "rootfile", {"full-path": "EPUB/package.opf", "media-type": "application/oebps-package+xml"})
            book.writestr("META-INF/container.xml", xml_bytes(container))
            book.writestr("EPUB/reader.css", "body{font-family:serif;line-height:1.8;}img{max-width:100%;height:auto;}p{margin:1em 0;}h1{font-weight:normal;}")
        for chapter in chapters:
            chapter_count += 1
            chapter_source = sorted({p["source"] for p in chapter.paragraphs})
            notes.append((chapter.title, "、".join(chapter_source)))
            if txt:
                txt.write(chapter.title + "\n\n")
            else:
                document, body = xhtml(chapter.title)
                ET.SubElement(body, "h1").text = xml_text(chapter.title)
            for paragraph in chapter.paragraphs:
                body_bytes += len(paragraph["text"].encode("utf-8"))
                if body_bytes > MAX_BODY:
                    raise Problem("EXPORT_SIZE_LIMIT", "正文导出超出 2 GiB 限额", 422)
                before = images.placements(paragraph, "before")
                after = images.placements(paragraph, "after")
                if txt:
                    # Resolve inline images for the source/license appendix;
                    # TXT prose stays free of badges and image/UI placeholders.
                    if paragraph["nodes"]:
                        scratch = ET.Element("div"); append_nodes(scratch, paragraph["nodes"], images)
                    txt.write(paragraph["text"] + "\n\n")
                else:
                    for row in before:
                        append_image(body, row)
                    if paragraph["nodes"]:
                        append_nodes(ET.SubElement(body, "div"), paragraph["nodes"], images)
                    else:
                        ET.SubElement(body, "p").text = xml_text(paragraph["text"])
                    for row in after:
                        append_image(body, row)
            if book:
                book.writestr("EPUB/chapter-" + str(chapter_count).zfill(5) + ".xhtml", xml_bytes(document))
        if not chapter_count:
            range_error()
        provenance = "来源说明\n" + source + "\n\n" + "\n".join(str(i + 1) + ". " + item[0] + "：" + item[1] for i, item in enumerate(notes))
        if images.images:
            provenance += "\n\n图片与授权\n" + "\n".join("插图 " + str(i + 1) + " · " + row["alt"] + " · " + row["source"] + " · " + row["license"] for i, row in enumerate(images.images.values()))
            if txt:
                provenance += "\nTXT 不嵌入图片，可另行保存所需原图。"
        if txt:
            txt.write(provenance + "\n")
        else:
            document, body = xhtml("来源说明")
            ET.SubElement(body, "h1").text = "来源说明"
            for part in provenance.split("\n"):
                if part:
                    ET.SubElement(body, "p").text = xml_text(part)
            book.writestr("EPUB/sources.xhtml", xml_bytes(document))
            package = ET.Element("package", {"xmlns": OPF, "version": "3.0", "unique-identifier": "publication-id", "xml:lang": "zh-CN"})
            meta = ET.SubElement(package, "metadata", {"xmlns:dc": DC})
            ET.SubElement(meta, "dc:identifier", {"id": "publication-id"}).text = "urn:uuid:" + str(UUID(m.uid()))
            ET.SubElement(meta, "dc:title").text = xml_text(title)
            ET.SubElement(meta, "dc:language").text = "zh-CN"
            ET.SubElement(meta, "dc:source").text = xml_text(source)
            ET.SubElement(meta, "meta", {"property": "dcterms:modified"}).text = m.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            manifest = ET.SubElement(package, "manifest")
            ET.SubElement(manifest, "item", {"id": "nav", "href": "nav.xhtml", "media-type": "application/xhtml+xml", "properties": "nav"})
            ET.SubElement(manifest, "item", {"id": "style", "href": "reader.css", "media-type": "text/css"})
            spine = ET.SubElement(package, "spine")
            nav, body = xhtml(title)
            navigation = ET.SubElement(body, "nav", {"epub:type": "toc", "id": "toc"})
            ET.SubElement(navigation, "h1").text = "目录"
            ordered = ET.SubElement(navigation, "ol")
            for i, (chapter_title, _) in enumerate(notes):
                key = "chapter-" + str(i + 1).zfill(5); href = key + ".xhtml"
                ET.SubElement(manifest, "item", {"id": key, "href": href, "media-type": "application/xhtml+xml"})
                ET.SubElement(spine, "itemref", {"idref": key})
                ET.SubElement(ET.SubElement(ordered, "li"), "a", {"href": href}).text = xml_text(chapter_title)
            ET.SubElement(manifest, "item", {"id": "sources", "href": "sources.xhtml", "media-type": "application/xhtml+xml"})
            ET.SubElement(spine, "itemref", {"idref": "sources"})
            ET.SubElement(ET.SubElement(ordered, "li"), "a", {"href": "sources.xhtml"}).text = "来源说明"
            for i, row in enumerate(images.images.values()):
                ET.SubElement(manifest, "item", {"id": "image-" + str(i + 1), "href": row["href"], "media-type": row["mediaType"]})
                book.write(root / row["href"], "EPUB/" + row["href"])
            book.writestr("EPUB/nav.xhtml", xml_bytes(nav)); book.writestr("EPUB/package.opf", xml_bytes(package))
    finally:
        if txt: txt.close()
        if book: book.close()
    content_hash = __import__("hashlib").sha256()
    with file.open("rb") as handle:
        while chunk := handle.read(1024**2): content_hash.update(chunk)
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", title[:100]).strip(". ") or "故事"
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", safe_title, re.IGNORECASE): safe_title = "故事-" + safe_title
    return {"format": data.format, "fileName": safe_title + "." + data.format, "sha256": content_hash.hexdigest(),
            "size": file.stat().st_size, "chapterCount": chapter_count, "imageCount": len(images.images),
            "scope": data.scope.model_dump()}


def export_public(operation):
    if operation.kind != "reading_export":
        raise Problem("RESOURCE_NOT_FOUND", "导出记录不存在或不可访问", 404)
    result = {"id": operation.id, "status": operation.status, "version": operation.version,
              "expiresAt": operation.expires_at.isoformat(), "protocol": PROTOCOL}
    for key in ("format", "fileName", "sha256", "size", "chapterCount", "imageCount"):
        if key in operation.result: result[key] = operation.result[key]
    if operation.error_code: result["errorCode"] = operation.error_code
    return result


def export_reading(db, owner, key, data):
    # Re-authorize even a replay before returning its private title or receipt.
    source_scope(db, owner, data)
    operation, fresh = create_operation(db, owner, "reading_export", key, data.model_dump())
    if not fresh:
        return export_public(operation)
    root = None
    committing = False
    result = None
    operation_id = operation.id
    try:
        root = allocate_stage(db, operation, owner)
        with SessionLocal() as snapshot:
            snapshot.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            result = write_reading(snapshot, owner, data, root)
        active(db, owner); operation = owned_operation(db, owner, operation.id, lock=True)
        operation.result = result; operation.status = "ready"; operation.version += 1
        committing = True
        db.commit()
        return export_public(operation)
    except BaseException as error:
        if committing:
            # COMMIT acknowledgment may fail after PG persisted the receipt.
            # Do not trust this Session's identity map or erase a durable file.
            db.close()
            try:
                with SessionLocal() as probe:
                    saved = probe.scalar(select(PortableOperation).where(PortableOperation.id == operation_id,
                        PortableOperation.owner_id == owner, PortableOperation.kind == "reading_export"))
                    if saved is not None and saved.status == "ready" and saved.result == result:
                        return export_public(saved)
            except (SQLAlchemyError, OSError):
                raise Problem("EXPORT_RESPONSE_UNKNOWN", "导出结果暂时无法确认，请查询这次已有请求", 503, retryable=True) from error
        if root is not None:
            _remove_owned_stage(root, root.parent, "reader-bk-download-")
        fail_operation(db, operation_id, owner, getattr(error, "code", "READING_EXPORT_FAILED"))
        if isinstance(error, ArchiveError):
            raise Problem("EXPORT_INLINE_INVALID", "正文格式无法安全导出，原文已保留", 422) from error
        raise


def download_reading(db, owner, identifier):
    operation = owned_operation(db, owner, identifier)
    export_public(operation)
    if operation.status != "ready" or operation.expires_at <= m.now():
        raise Problem("EXPORT_DOWNLOAD_EXPIRED", "导出文件已失效，请重新导出", 409)
    # A deleted source or line revokes its previously issued download as well.
    scope = operation.result.get("scope")
    if not isinstance(scope, dict):
        raise Problem("RESOURCE_NOT_FOUND", "导出文件不可用", 404)
    data = ReadingExportInput.model_validate({"format": operation.result["format"], "scope": scope,
                                             "range": "selected", "startChapter": 1, "endChapter": 1, "allowUnseen": True})
    source_scope(db, owner, data)
    file = stage_path(owner, operation.stage_name) / ("reading." + data.format)
    if not file.is_file() or file.is_symlink() or file.stat().st_size != operation.result["size"]:
        raise Problem("RESOURCE_NOT_FOUND", "导出文件不可用", 404)
    return file, operation.result
