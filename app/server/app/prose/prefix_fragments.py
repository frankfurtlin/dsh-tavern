"""Literal immutable paragraph prefixes with explicit, replayable source proof."""
from ..domain import digest, utf16_len, utf16_slice
from ..errors import Problem
from ..models import ContentBlock, Revision, uid

SCHEMA = 'paragraph-prefix-v1'
FIELDS = {'blockId', 'prefixSourceBlockId', 'prefixSourceRevisionId', 'limit', 'sourceHash', 'prefixHash'}


def create_fragment(db, owner, book_id, source_revision, block, limit):
    if (block.owner_id != owner or block.book_id != book_id
            or source_revision.owner_id != owner
            or digest(block.text) != block.content_hash
            or not any(c['id'] == block.chapter_id and block.id in c['blocks']
                for c in source_revision.manifest)):
        raise ValueError('paragraph prefix source scope')
    if type(limit) is not int or not 0 < limit < utf16_len(block.text):
        raise ValueError('paragraph prefix boundary')
    literal = utf16_slice(block.text, 0, limit)
    fragment = ContentBlock(id=uid(), owner_id=owner, book_id=book_id,
        chapter_id=block.chapter_id, text=literal, html=None,
        content_hash=digest(literal), source=block.source)
    proof = {'blockId': fragment.id, 'prefixSourceBlockId': block.id,
        'prefixSourceRevisionId': source_revision.id, 'limit': limit,
        'sourceHash': block.content_hash, 'prefixHash': fragment.content_hash}
    db.add(fragment)
    return fragment, proof


def validate_revision_fragments(revision, lookup):
    """Verify against exact parent objects, including during restore preflight."""
    provenance = revision.provenance
    if not isinstance(provenance, dict):
        raise ValueError('paragraph prefix provenance')
    marker, proofs = provenance.get('paragraphPrefixSchema'), provenance.get('paragraphPrefixes')
    if marker is None and proofs is None:
        return {}
    if marker != SCHEMA or not isinstance(proofs, list) or len(proofs) != 1:
        raise ValueError('paragraph prefix schema')
    aliases = {}
    for proof in proofs:
        if not isinstance(proof, dict) or set(proof) != FIELDS:
            raise ValueError('paragraph prefix shape')
        if any(not isinstance(proof[k], str) or not proof[k] for k in
                ('blockId', 'prefixSourceBlockId', 'prefixSourceRevisionId')):
            raise ValueError('paragraph prefix reference')
        fragment = lookup(ContentBlock, proof['blockId'])
        original = lookup(ContentBlock, proof['prefixSourceBlockId'])
        parent = lookup(Revision, proof['prefixSourceRevisionId'])
        if (fragment is None or original is None or parent is None
                or parent.id != revision.parent_revision_id
                or fragment.id == original.id or fragment.owner_id != revision.owner_id
                or original.owner_id != revision.owner_id or parent.owner_id != revision.owner_id
                or fragment.book_id != original.book_id or fragment.chapter_id != original.chapter_id
                or fragment.source != original.source
                or type(proof['limit']) is not int or not 0 < proof['limit'] < utf16_len(original.text)
                or not any(c['id'] == original.chapter_id and original.id in c['blocks'] for c in parent.manifest)
                or any(fragment.id in c['blocks'] for c in parent.manifest)
                or sum(c['blocks'].count(fragment.id) for c in revision.manifest) != 1):
            raise ValueError('paragraph prefix source membership')
        try:
            literal = utf16_slice(original.text, 0, proof['limit'])
        except Problem as error:
            raise ValueError('paragraph prefix unicode boundary') from error
        if (digest(original.text) != original.content_hash or proof['sourceHash'] != original.content_hash
                or fragment.text != literal
                or fragment.html is not None or digest(fragment.text) != fragment.content_hash
                or proof['prefixHash'] != fragment.content_hash):
            raise ValueError('paragraph prefix exact bytes')
        aliases[fragment.id] = original.id
    return aliases


def fragment_roots(chain, lookup):
    aliases = {}
    for revision in reversed(chain):
        for child, parent in validate_revision_fragments(revision, lookup).items():
            if child in aliases:
                raise ValueError('paragraph prefix repeated origin')
            aliases[child] = parent
    roots = {}
    for identifier in aliases:
        root, seen = identifier, set()
        while root in aliases:
            if root in seen or len(seen) >= 100:
                raise ValueError('paragraph prefix cyclic source')
            seen.add(root)
            root = aliases[root]
        roots[identifier] = root
    return roots
