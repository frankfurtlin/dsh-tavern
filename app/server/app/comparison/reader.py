"""Immutable reader paths, clipped to the exact observed UTF-16 boundary."""
from sqlalchemy import select
from .. import models as m
from ..api_books import get_revision
from ..character_cards import filter_data
from ..domain import owned, digest, canonical, validate_anchor, make_anchor, utf16_len, utf16_slice
from ..errors import Problem
from ..knowledge import ANALYZER, snapshot_hash
from .common import exposures

MAX_BLOCKS = 200000


def verify_import(db, owner, book, revision, entries):
    # Original imports hash ordered (text, html), rather than persisted IDs.
    # Confirming a different chapter order changes the manifest, not that hash.
    provenance = revision.provenance
    if (not isinstance(provenance, dict) or provenance.get("type") != "import" or
            provenance.get("sourceId") != book.source_id or book.original_revision_id != revision.id):
        raise Problem("REVISION_INVALID", "正文修订校验未通过", 409)
    source = db.scalar(select(m.SourceDocument).where(m.SourceDocument.owner_id == owner,
        m.SourceDocument.id == book.source_id, m.SourceDocument.book_id == book.id))
    mapping = source.source_map.get("blocks") if source and isinstance(source.source_map, dict) else None
    expected = {block: chapter for chapter, _, block in entries}
    if not isinstance(mapping, dict) or set(mapping) != set(expected):
        raise Problem("REVISION_INVALID", "导入正文来源校验未通过", 409)
    positions = {}
    for block, value in mapping.items():
        start = value.get("normalizedStart") if isinstance(value, dict) else None
        end = value.get("normalizedEnd") if isinstance(value, dict) else None
        if type(start) is not int or type(end) is not int or start < 0 or end < start:
            raise Problem("REVISION_INVALID", "导入正文来源校验未通过", 409)
        positions[block] = start
    if len(set(positions.values())) != len(positions):
        raise Problem("REVISION_INVALID", "导入正文来源校验未通过", 409)
    blocks = {}
    identifiers = list(expected)
    # Bound SQL parameters, including for large imports. Never read private
    # source files or task journals, and never write a replacement hash.
    for offset in range(0, len(identifiers), 1024):
        for block in db.scalars(select(m.ContentBlock).where(m.ContentBlock.owner_id == owner,
                m.ContentBlock.book_id == book.id, m.ContentBlock.id.in_(identifiers[offset:offset+1024]))):
            if (block.chapter_id != expected[block.id] or block.source != "original" or
                    block.content_hash != digest(block.text)):
                raise Problem("REVISION_INVALID", "导入正文段落校验未通过", 409)
            blocks[block.id] = block
    if set(blocks) != set(expected):
        raise Problem("REVISION_INVALID", "导入正文段落校验未通过", 409)
    ordered = sorted(blocks, key=positions.__getitem__)
    if revision.content_hash != digest(canonical([(blocks[key].text, blocks[key].html) for key in ordered])):
        raise Problem("REVISION_INVALID", "正文修订校验未通过", 409)


def manifest(db, owner, book, revision):
    result = []
    try:
        if not isinstance(revision.manifest, list): raise ValueError()
        chapter_ids = set()
        for chapter in revision.manifest:
            if (not isinstance(chapter, dict) or not isinstance(chapter["id"], str) or
                    chapter["id"] in chapter_ids or not isinstance(chapter["title"], str) or
                    not isinstance(chapter["blocks"], list) or
                    any(not isinstance(block, str) for block in chapter["blocks"])): raise ValueError()
            chapter_ids.add(chapter["id"])
            result.extend((chapter["id"], chapter["title"], block) for block in chapter["blocks"])
            if len(result) > MAX_BLOCKS:
                raise Problem("COMPARE_GRAPH_LIMIT", "请比较更短的故事路径", 422)
    except (ValueError, KeyError, TypeError):
        raise Problem("REVISION_INVALID", "正文修订结构校验未通过", 409)
    if len({row[2] for row in result}) != len(result):
        raise Problem("REVISION_INVALID", "正文修订存在重复段落", 409)
    if revision.content_hash != digest(canonical(revision.manifest)):
        verify_import(db, owner, book, revision, result)
    return result


def location(db, owner, book_id, line_id, node_id):
    book = owned(db, m.Book, book_id, owner)
    branch, revision = get_revision(db, line_id, node_id, owner)
    if book.status == "deleted" or branch.status == "deleted" or branch.book_id != book.id:
        raise Problem("RESOURCE_NOT_FOUND", "这条阅读路径不存在或不可访问", 404)
    return book, branch, revision, manifest(db, owner, book, revision)


def boundary(db, owner, book, branch, revision, entries):
    candidates = [row.max_observed_anchor for row in db.scalars(select(m.Progress).where(
        m.Progress.owner_id == owner, m.Progress.book_id == book.id,
        m.Progress.branch_id == branch.id, m.Progress.revision_id == revision.id))]
    candidates += [row.anchor for row in exposures(db, owner, "book", book.id, branch.id)
                   if row.node_id == revision.id]
    order = {block: index for index, (_, _, block) in enumerate(entries)}
    valid = []
    for value in candidates:
        _, selected, _ = validate_anchor(db, value, owner)
        if selected.id != revision.id:
            raise Problem("ANCHOR_INVALID", "已读位置与所选修订不一致", 409)
        valid.append(value)
    return max(valid, key=lambda value: (order[value["blockId"]], value["end"]), default=None)


def known_lengths(db, owner, entries, anchor):
    if not anchor:
        return {}
    index = next(i for i, row in enumerate(entries) if row[2] == anchor["blockId"])
    return {row[2]: (anchor["end"] if i == index else None) for i, row in enumerate(entries[:index+1])}


def visible_facts(db, owner, revision, anchor, entries):
    if not anchor:
        return None
    # Verify the evidence cache without creating cards, aliases or model work.
    rows = db.scalars(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id == owner,
        m.KnowledgeSnapshot.revision_id == revision.id, m.KnowledgeSnapshot.completeness == "complete",
        m.KnowledgeSnapshot.analyzer_version == ANALYZER).order_by(m.KnowledgeSnapshot.created_at.desc()).limit(20))
    for row in rows:
        try:
            profile_id, version = row.profile_version.split(":")
            profile = db.scalar(select(m.ModelProfile).where(m.ModelProfile.owner_id == owner,
                m.ModelProfile.id == profile_id, m.ModelProfile.version == int(version)))
            if not profile:
                continue
            _, source_revision, _ = validate_anchor(db, row.boundary, owner)
            if source_revision.id != revision.id:
                continue
            known = known_lengths(db, owner, entries, row.boundary)
            blocks = {block.id: block for block in db.scalars(select(m.ContentBlock).where(
                m.ContentBlock.owner_id == owner, m.ContentBlock.id.in_(known)))}
            prefix = [{"blockId": block_id, "chapterId": chapter, "text": blocks[block_id].text if known[block_id] is None
                else utf16_slice(blocks[block_id].text, 0, known[block_id]), "source": blocks[block_id].source}
                for chapter, _, block_id in entries if block_id in known
                and (known[block_id] is None or known[block_id] > 0)]
            if row.scope_hash != snapshot_hash(row.boundary, prefix, profile):
                continue
            data = filter_data(row, anchor, revision)
            aliases = {alias.entity_id: alias.identity_id for alias in db.scalars(select(m.CharacterAlias).where(
                m.CharacterAlias.owner_id == owner, m.CharacterAlias.scope_kind == "reader",
                m.CharacterAlias.scope_id == revision.branch_id,
                m.CharacterAlias.entity_id.in_([char["id"] for char in data["characters"]])))}
            identities = {char["id"]: aliases.get(char["id"], revision.branch_id+":"+char["id"])
                          for char in data["characters"]}
            facts = {}
            for fact in data["facts"]:
                key = identities[fact["subjectId"]]+":"+fact["predicate"]
                facts.setdefault(key, []).append({field: fact[field] for field in ("value", "status") if field in fact})
            for values in facts.values(): values.sort(key=canonical)
            return {"characters": {identities[char["id"]]: {"name": char["displayName"]} for char in data["characters"]},
                "facts": facts}
        except (ValueError, KeyError, TypeError, Problem):
            continue
    return None


def side(db, owner, book_id, line_id, node_id, offset, limit):
    book, branch, revision, entries = location(db, owner, book_id, line_id, node_id)
    anchor = boundary(db, owner, book, branch, revision, entries)
    known = known_lengths(db, owner, entries, anchor)
    readable = [row for row in entries if row[2] in known and known[row[2]] != 0]
    page = readable[offset:offset+limit]
    blocks = {row.id: row for row in db.scalars(select(m.ContentBlock).where(m.ContentBlock.owner_id == owner,
        m.ContentBlock.book_id == book.id, m.ContentBlock.id.in_([row[2] for row in page])))}
    items = []
    for chapter, title, block_id in page:
        block = blocks.get(block_id)
        if not block or block.chapter_id != chapter or block.content_hash != digest(block.text):
            raise Problem("CONTENT_INVALID", "正文段落校验未通过", 409)
        end = known[block_id]
        items.append({"id": block.id, "chapterId": chapter, "title": title,
            "text": block.text if end is None else utf16_slice(block.text, 0, end),
            "partial": end is not None and end < utf16_len(block.text)})
    exposure = next((row for row in exposures(db, owner, "book", book.id, branch.id) if row.node_id == revision.id), None)
    tail_partial = False
    if readable and known[readable[-1][2]] is not None:
        tail_block = blocks.get(readable[-1][2]) or owned(db, m.ContentBlock, readable[-1][2], owner)
        tail_partial = known[tail_block.id] < utf16_len(tail_block.text)
    value = {"lineId": branch.id, "name": branch.name, "nodeId": revision.id, "viewVersion": exposure.version if exposure else 0,
        "requestedNodeId": revision.id, "observedNodeId": anchor["blockId"] if anchor else None,
        "hasUnseen": len(readable) < len(entries) or tail_partial,
        "items": items, "nextOffset": offset+len(items) if offset+len(items) < len(readable) else None,
        "state": visible_facts(db, owner, revision, anchor, entries)}
    return value, entries, known, branch


def compare(db, owner, data):
    left, a, a_known, a_branch = side(db, owner, data.bookId, data.leftLineId, data.leftNodeId, data.leftOffset, data.limit)
    right, b, b_known, b_branch = side(db, owner, data.bookId, data.rightLineId, data.rightNodeId, data.rightOffset, data.limit)
    common = None
    for before, after in zip(a, b):
        if before[2] != after[2]:
            break
        common = before
    visible = bool(common and common[2] in a_known and common[2] in b_known)
    start = {"nodeId": common[2] if common else None, "visible": visible,
        "title": common[1] if visible else None, "anchor": None}
    # Partial-paragraph forks have an exact source anchor even when that block
    # is replaced on the child. A title or identical prose is never identity.
    for child, parent in ((a_branch, a_branch.parent_branch_id), (b_branch, b_branch.parent_branch_id)):
        opposite = b_branch if child.id == a_branch.id else a_branch
        if parent != opposite.id or not child.fork_anchor:
            continue
        _, origin, _ = validate_anchor(db, child.fork_anchor, owner)
        other_node = right["nodeId"] if child.id == a_branch.id else left["nodeId"]
        if origin.id == other_node:
            source_known = b_known if child.id == a_branch.id else a_known
            fork = child.fork_anchor
            length = source_known.get(fork["blockId"], -1)
            if fork["blockId"] in source_known and (length is None or length >= fork["end"]):
                start["anchor"] = fork
    return left, right, start
