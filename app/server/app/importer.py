import io
import json
import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from html import escape
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes
from defusedxml import ElementTree as ET
from PIL import Image
from .domain import digest, utf16_len
from .errors import Problem
from .storage import write_private, _write_task_journal

MAX_UPLOAD = 100 * 1024 * 1024
MAX_CHARACTERS = 3_000_000
ENCODINGS = {"utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "gbk", "big5"}


def normalize(text):
    if "\x00" in text or any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise Problem("INVALID_TEXT", "材料包含非法字符")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def txt_parse(data, encoding=None, *, markdown=False):
    warnings = []
    if encoding and encoding not in ENCODINGS:
        raise Problem("ENCODING_INVALID", "所选编码不支持")
    if not encoding:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        else:
            try:
                data.decode("utf-8-sig")
                encoding = "utf-8-sig"
            except UnicodeDecodeError:
                detected = from_bytes(data).best()
                encoding = detected.encoding if detected else "gb18030"
                warnings.append("编码需要确认；可选择 GB18030、GBK、Big5 或 UTF-16 后重新预览")
    try:
        original = data.decode(encoding, errors="strict")
    except (UnicodeError, LookupError):
        raise Problem("ENCODING_INVALID", "材料无法按所选编码解码，请重新选择编码")
    text = normalize(original)
    chapters, blocks = [], []
    heading = re.compile(r"^\s*(?:第[零〇一二三四五六七八九十百千万0-9]+[章节卷回部].*|chapter\s+\d+.*)\s*$", re.I)
    paragraph, start_offset, chapter = [], 0, None
    raw_encoding = encoding
    bom_bytes = 0
    if encoding == "utf-8-sig":
        raw_encoding, bom_bytes = "utf-8", 3 if data.startswith(b"\xef\xbb\xbf") else 0
    elif encoding == "utf-16":
        raw_encoding, bom_bytes = ("utf-16-le" if data.startswith(b"\xff\xfe") else "utf-16-be"), 2
    source_location = {}

    def flush():
        nonlocal paragraph, chapter
        if not paragraph:
            return
        if chapter is None:
            chapter = {"id": digest(f"chapter:{len(chapters)}")[:32], "title": f"第 {len(chapters) + 1} 章", "blocks": []}
            chapters.append(chapter)
        content = "\n".join(paragraph)
        bid = digest(f"block:{len(blocks)}:{content}")[:32]
        blocks.append({"id": bid, "chapterId": chapter["id"], "text": content, "html": None, "sourceOffset": start_offset, "sourceLocation": dict(source_location)})
        chapter["blocks"].append(bid)
        paragraph = []

    offset, decoded_offset, byte_offset, body_characters = 0, 0, bom_bytes, 0
    for line, original_line in zip(text.splitlines(keepends=True), original.splitlines(keepends=True)):
        clean = line.removesuffix("\n")
        markdown_heading = re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*#*$', clean) if markdown else None
        if heading.match(clean) or markdown_heading:
            flush()
            chapter = {"id": digest(f"chapter:{len(chapters)}")[:32], "title": markdown_heading.group(1).strip() if markdown_heading else clean.strip(), "blocks": []}
            chapters.append(chapter)
        elif not clean.strip():
            flush()
        else:
            body_characters += len(clean)
            if body_characters > MAX_CHARACTERS:
                raise Problem("IMPORT_LIMIT", "正文超过 300 万字符限额")
            if not paragraph:
                start_offset = offset
                source_location = {"decodedStart": decoded_offset, "decodedEnd": decoded_offset+utf16_len(clean), "decodedOffsetUnit": "utf16",
                    "byteStart": byte_offset, "byteEnd": byte_offset+len(clean.encode(raw_encoding))}
            paragraph.append(clean)
            # Stable paragraph segmentation for single-newline novels.
            flush()
        offset += len(line)
        decoded_offset += utf16_len(original_line)
        byte_offset += len(original_line.encode(raw_encoding))
    flush()
    chapters = [c for c in chapters if c["blocks"]]
    if not blocks:
        raise Problem("EMPTY_DOCUMENT", "材料没有可阅读正文")
    if len(chapters) > 10000:
        raise Problem("IMPORT_LIMIT", "章节超过 10000 章限额")
    return {"chapters": chapters, "blocks": blocks, "encoding": encoding, "warnings": warnings,
            "normalized": text, "sourceMap": {"normalization": "CRLF/CR to LF", "offsetUnit": "codepoint", "bomRemoved": original.startswith("\ufeff")}}


def safe_member(name):
    if "\\" in name or name.startswith("/") or ":" in name or ".." in PurePosixPath(name).parts:
        raise Problem("UNSAFE_ARCHIVE", "EPUB 包含不安全路径")
    return name


def epub_parse(data, owner_id, import_id):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > 200 * 1024 * 1024:
            raise Problem("IMPORT_LIMIT", "EPUB 超过解压限额")
        seen = set()
        for entry in entries:
            safe_member(entry.filename)
            if entry.filename in seen:
                raise Problem("UNSAFE_ARCHIVE", "EPUB 包含重复资源")
            seen.add(entry.filename)
            if entry.file_size > 20 * 1024 * 1024 or entry.file_size / max(1, entry.compress_size) > 100:
                raise Problem("IMPORT_LIMIT", "EPUB 资源超过大小或压缩比限额")
            if entry.flag_bits & 1:
                raise Problem("DRM_UNSUPPORTED", "不支持加密 EPUB")
        if "META-INF/encryption.xml" in seen:
            raise Problem("DRM_UNSUPPORTED", "不支持带加密资源的 EPUB")
        if archive.read("mimetype").strip() != b"application/epub+zip":
            raise Problem("EPUB_INVALID", "EPUB 类型声明无效")
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None:
            raise Problem("EPUB_INVALID", "EPUB 缺少根文档")
        opf_path = safe_member(rootfile.attrib["full-path"])
        base = posixpath.dirname(opf_path)
        opf = ET.fromstring(archive.read(opf_path))
        if opf.attrib.get("version") not in {"2.0", "3.0", "3.1", "3.2", "3.3"}:
            raise Problem("EPUB_VERSION_UNSUPPORTED", "EPUB 版本不支持")
        items = {e.attrib["id"]: e.attrib for e in opf.findall(".//{*}manifest/{*}item")}
        spine = opf.findall(".//{*}spine/{*}itemref")
        if not spine:
            raise Problem("EPUB_SPINE_MISSING", "EPUB 缺少阅读顺序")
        title_node = opf.find(".//{*}title")
        title = title_node.text if title_node is not None else None
        chapters, blocks, normalized, assets = [], [], [], {}
        normalized_length = 0
        for ci, ref in enumerate(spine):
            item = items.get(ref.attrib.get("idref"))
            if not item or item.get("media-type") not in ("application/xhtml+xml", "text/html"):
                raise Problem("EPUB_INVALID", "EPUB 阅读顺序引用无效")
            href = item["href"].split("#")[0]
            safe_member(href)
            page_path = safe_member(posixpath.normpath(posixpath.join(base, href)))
            soup = BeautifulSoup(archive.read(page_path), "html.parser")
            for unsafe in soup(["script", "style", "form", "iframe", "object", "embed", "svg", "link", "meta", "input", "button", "audio", "video"]):
                unsafe.decompose()
            heading = soup.find(["h1", "h2", "h3"])
            chapter = {"id": digest(f"epub-chapter:{ci}")[:32], "title": heading.get_text() if heading else f"第 {ci + 1} 章", "blocks": []}
            root = soup.body or soup
            elements = root.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote", "img"])
            elements = [e for e in elements if not any(p.name in ("p", "li", "blockquote") for p in e.parents)]
            if not elements:
                elements = [root]
            for element_index, element in enumerate(elements):
                if element is heading:
                    continue
                for img in ([element] if element.name == "img" else element.find_all("img")):
                    source = img.get("src", "")
                    if not source or ":" in source or source.startswith(("//", "/")):
                        img.decompose()
                        continue
                    resource = safe_member(posixpath.normpath(posixpath.join(posixpath.dirname(page_path), source)))
                    raw = archive.read(resource)
                    with Image.open(io.BytesIO(raw)) as picture:
                        if picture.width * picture.height > 25_000_000:
                            raise Problem("IMPORT_LIMIT", "EPUB 图片过大")
                        picture = picture.convert("RGB")
                        output = io.BytesIO()
                        picture.save(output, format="PNG")
                    asset_id = digest(output.getvalue())[:32]
                    asset_name = f"asset-{import_id}-{asset_id}.png"
                    _write_task_journal(owner_id, import_id, asset_name, output.getvalue())
                    assets[asset_id] = asset_name
                    img.attrs = {"data-asset": asset_id, "alt": str(img.get("alt", "插图"))[:100]}
                for tag in [element, *element.find_all(True)]:
                    if tag.name == "img":
                        continue
                    if tag.name not in {"p", "div", "body", "em", "strong", "b", "i", "br", "span", "blockquote", "li", "h1", "h2", "h3", "h4"}:
                        tag.unwrap()
                    else:
                        tag.attrs = {}
                text = normalize(element.get_text("", strip=False))
                markup = str(element)
                if not text.strip() and "data-asset" not in markup:
                    continue
                bid = digest(f"epub-block:{len(blocks)}:{text}")[:32]
                blocks.append({"id": bid, "chapterId": chapter["id"], "text": text, "html": markup, "sourceOffset": normalized_length,
                    "sourceLocation": {"spineIndex": ci, "sourceHref": page_path, "elementIndex": element_index}})
                normalized.append(text)
                normalized_length += len(text)+1
                chapter["blocks"].append(bid)
            if chapter["blocks"]:
                chapters.append(chapter)
        if not blocks or len(chapters) > 10000:
            raise Problem("EPUB_INVALID", "EPUB 没有有效正文或章节超限")
        return {"title": title, "chapters": chapters, "blocks": blocks, "encoding": "utf-8", "warnings": [],
                "normalized": "\n".join(normalized), "assets": assets, "sourceMap": {"spine": [r.attrib["idref"] for r in spine]}}
    except Problem:
        raise
    except Exception:
        raise Problem("EPUB_INVALID", "EPUB 文件损坏或资源不完整")


def parse(data, filename, owner_id, import_id, encoding=None):
    if len(data) > MAX_UPLOAD:
        raise Problem("IMPORT_LIMIT", "上传超过 100 MiB")
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return txt_parse(data, encoding, markdown=suffix != ".txt")
    if suffix == ".epub":
        return epub_parse(data, owner_id, import_id)
    raise Problem("UNSUPPORTED_FORMAT", "支持 TXT、Markdown 与无 DRM EPUB")
