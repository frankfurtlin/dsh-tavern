"""Author documents and player/reader evidence are deliberately distinct queries."""
import io
import re
import posixpath
from pathlib import Path
from urllib.parse import urlsplit, unquote
from zipfile import ZipFile, BadZipFile
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from sqlalchemy import select
from ..errors import Problem
from ..models import Revision, Branch
from ..domain import owned, validate_anchor
from ..knowledge import safe_scope
from .models import KnowledgeDoc, KnowledgeChunk


def epub_path(base, href):
    link = urlsplit(href)
    if link.scheme or link.netloc:
        raise ValueError("External EPUB resource")
    path = posixpath.normpath(posixpath.join(base, unquote(link.path)))
    if path.startswith(("/", "../")) or path == ".." or "\\" in path:
        raise ValueError("Invalid EPUB resource path")
    return path


def extract(name, content):
    extension = Path(name).suffix.lower()
    if extension in (".txt", ".md"):
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                return content.decode("gb18030")
            except UnicodeDecodeError:
                raise Problem("KNOWLEDGE_ENCODING_INVALID", "请使用 UTF-8 或 GB18030 文本")
    if extension not in (".docx", ".epub"):
        raise Problem("KNOWLEDGE_FORMAT_INVALID", "知识库支持 TXT、MD、DOCX、EPUB")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > 32 * 1024 * 1024:
                raise ValueError()
            if extension == ".docx":
                root = ElementTree.fromstring(archive.read("word/document.xml"))
                paragraphs = root.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
                return "\n".join("".join(p.itertext()) for p in paragraphs)
            # Extract only ordered manifest documents from the EPUB spine.
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            package_name = epub_path("", next(e.attrib["full-path"] for e in container.iter() if e.tag.endswith("rootfile")))
            package = ElementTree.fromstring(archive.read(package_name))
            manifest = {e.attrib["id"]: e.attrib["href"] for e in package.iter() if e.tag.endswith("}item")}
            base = posixpath.dirname(package_name)
            names = [epub_path(base, manifest[e.attrib["idref"]]) for e in package.iter() if e.tag.endswith("}itemref")]
            output = []
            for document in names:
                soup = BeautifulSoup(archive.read(document), "html.parser")
                for node in soup(["script", "style"]):
                    node.decompose()
                output.append(soup.get_text("\n", strip=True))
            return "\n".join(output)
    except (BadZipFile, KeyError, ValueError, StopIteration, RuntimeError, OSError, ElementTree.ParseError, DefusedXmlException):
        raise Problem("KNOWLEDGE_PARSE_FAILED", "文档无法解析或解压后过大")


def create_document(db, owner, obj, content):
    text = extract(obj.name, content).strip()
    if not text or len(text) > 1000000:
        raise Problem("KNOWLEDGE_CONTENT_INVALID", "知识文档为空或超过 100 万字")
    existing = db.scalar(select(KnowledgeDoc).where(KnowledgeDoc.owner_id == owner, KnowledgeDoc.source_version == obj.checksum, KnowledgeDoc.status == "ready"))
    if existing:
        return existing
    row = KnowledgeDoc(owner_id=owner, object_id=obj.id, title=obj.name, source_version=obj.checksum)
    db.add(row)
    db.flush()
    for index, start in enumerate(range(0, len(text), 700)):
        db.add(KnowledgeChunk(doc_id=row.id, ordinal=index, text=text[start:start + 800]))
    db.flush()
    return row


def query_terms(query):
    tokens = re.findall(r"[\w]+", query.lower())[:8]
    # Exact phrases carry more weight; adjacent CJK pairs allow natural Chinese searches.
    pairs = [text[i:i + 2] for text in re.findall(r"[\u3400-\u9fff]+", query) for i in range(len(text) - 1)]
    return list(dict.fromkeys(tokens + pairs))[:32]


def rank(query, candidates):
    tokens = query_terms(query)
    hits = []
    for candidate in candidates:
        score = sum(candidate["text"].lower().count(t) * len(t) for t in tokens)
        if score:
            hits.append({**candidate, "score": score})
    return sorted(hits, key=lambda h: (-h["score"], h.get("blockId", "")))[:20]


def search_documents(db, owner, query):
    terms = query_terms(query)
    if not terms:
        return []
    from sqlalchemy import or_, func
    lower = func.lower(KnowledgeChunk.text)
    score = sum(func.length(lower) - func.length(func.replace(lower, t, "")) for t in terms)
    rows = db.execute(select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc).where(KnowledgeDoc.owner_id == owner,
        KnowledgeDoc.status == "ready", KnowledgeDoc.scope == "author",
        or_(*(KnowledgeChunk.text.ilike("%" + t.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\") for t in terms)))
        .order_by(score.desc(), KnowledgeChunk.id).limit(500)).all()
    return rank(query, [{"text": c.text, "blockId": c.id, "documentId": d.id, "title": d.title,
                         "sourceVersion": d.source_version, "scope": "author"} for c, d in rows])


def search_reading(db, owner, query, anchor):
    branch, revision, block = validate_anchor(db, anchor, owner)
    blocks = safe_scope(db, anchor, revision, owner)
    return rank(query, [{**b, "sourceVersion": revision.id, "scope": "read"} for b in blocks])


def search_player(db, owner, query, world_id, line_id):
    from ..world.service import require_line, checked_checkpoint
    world, line = require_line(db, owner, world_id, line_id, active=True)
    cp = checked_checkpoint(db, owner, world.id, line.head_checkpoint_id)
    player = cp.state["playerId"]
    known = set(cp.state["entities"][player].get("known", []))
    candidates = [{"text": item["text"], "blockId": item["id"], "sourceVersion": cp.id, "scope": "player"}
                  for item in cp.definition.get("lore", []) if item["id"] in known]
    from ..world.state import public_payload
    return public_payload({'items':rank(query, candidates)}, cp.state, cp.definition)['items']
